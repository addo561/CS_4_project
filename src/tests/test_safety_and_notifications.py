import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure src directory is in Python path
_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.abspath(os.path.join(_DIR, ".."))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from core.action_engine import ActionEngine

class MockProcess:
    def __init__(self, pid, name, ppid=99999, status="running", username="user", uids=None):
        self.pid = pid
        self._ppid = ppid
        self._status = status
        self._name = name
        self._username = username
        self._uids = uids or type('uids', (object,), {'real': 1000})()
        self._mem_info = type('mem_info', (object,), {'rss': 1024 * 1024 * 10})()
        self.info = {
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "status": status,
            "username": username,
            "uids": self._uids,
            "memory_info": self._mem_info,
            "memory_percent": 1.0,
            "cpu_percent": 0.0
        }

    def ppid(self):
        return self._ppid

    def status(self):
        return self._status

    def name(self):
        return self._name

    def username(self):
        return self._username

    def uids(self):
        return self._uids

    def memory_info(self):
        return self._mem_info

    def memory_percent(self):
        return 1.0

    def cpu_percent(self):
        return 0.0


def check_optimizer_service_notifications():
    path = os.path.join(_SRC_DIR, "optimizer_service.py")
    if not os.path.exists(path):
        return False, f"Could not find optimizer_service.py at {path}"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    idx = content.find('"toggle_optimizer"')
    if idx == -1:
        idx = content.find("'toggle_optimizer'")
    if idx == -1:
        return False, "Could not find toggle_optimizer command in optimizer_service.py"
    
    subcontent = content[idx:idx+800]
    lines = subcontent.splitlines()
    for line in lines:
        if "_notifier.send(" in line:
            stripped = line.strip()
            if not stripped.startswith("#"):
                return False, f"Found active notification call in optimizer_service.py: {stripped}"
    return True, "No active _notifier.send calls found in toggle_optimizer logic."


def check_dashboard_notifications():
    path = os.path.join(_SRC_DIR, "dashboard.py")
    if not os.path.exists(path):
        return False, f"Could not find dashboard.py at {path}"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        
    if "from core.notifier import Notifier" not in content:
        return False, "from core.notifier import Notifier import not found in dashboard.py"
    if "notifier = Notifier()" not in content:
        return False, "notifier = Notifier() instantiation not found in dashboard.py"
    if "notifier.send(" not in content:
        return False, "notifier.send() call not found in dashboard.py"
    return True, "Dashboard notifications check passed."


class TestSafetyAndNotifications(unittest.TestCase):
    
    def test_whitelist_coverage(self):
        engine = ActionEngine()
        # Ensure whitelist covers all apps
        self.assertGreaterEqual(len(engine._PROTECTIVE_LIST), 65)
        for app in engine._PROTECTIVE_LIST:
            # Test direct name match
            proc = MockProcess(pid=9999, name=app)
            self.assertFalse(engine._is_safe_to_suspend(proc, user_whitelist_lower=set()))
            
            # Test with .exe suffix
            proc_exe = MockProcess(pid=9999, name=f"{app}.exe")
            self.assertFalse(engine._is_safe_to_suspend(proc_exe, user_whitelist_lower=set()))

        # Test non-whitelisted app
        proc_safe = MockProcess(pid=9999, name="heavy_game")
        self.assertTrue(engine._is_safe_to_suspend(proc_safe, user_whitelist_lower=set()))

    @unittest.skipUnless(
        sys.platform in ("darwin", "win32"),
        "foreground-app detection is only implemented on macOS/Windows")
    @patch('psutil.process_iter')
    def test_foreground_protection(self, mock_process_iter):
        engine = ActionEngine()
        
        # Mock foreground PIDs
        engine._get_macos_foreground_pid = MagicMock(return_value=12345)
        engine._get_windows_foreground_pid = MagicMock(return_value=12345)
        
        proc_fg = MockProcess(pid=12345, name="heavy_app")
        proc_bg = MockProcess(pid=54321, name="idle_app")
        mock_process_iter.return_value = [proc_fg, proc_bg]
        
        # Test direct call to _is_safe_to_suspend with foreground_pid
        self.assertFalse(engine._is_safe_to_suspend(proc_fg, user_whitelist_lower=set(), foreground_pid=12345))
        self.assertTrue(engine._is_safe_to_suspend(proc_bg, user_whitelist_lower=set(), foreground_pid=12345))
        
        # Test select_targets skips the active foreground process
        with patch('config.load_user_whitelist', return_value=set()):
            targets = engine._select_targets(max_targets=3)
            target_pids = [p.pid for p in targets]
            self.assertIn(54321, target_pids)
            self.assertNotIn(12345, target_pids)

    @patch('psutil.Process')
    def test_self_protection(self, mock_process_class):
        my_pid = os.getpid()
        
        mock_proc_self = MagicMock()
        mock_proc_self.pid = my_pid
        
        mock_proc_parent = MagicMock()
        mock_proc_parent.pid = 4000
        
        mock_proc_grandparent = MagicMock()
        mock_proc_grandparent.pid = 3000
        
        mock_proc_root = MagicMock()
        mock_proc_root.pid = 1
        
        mock_proc_self.parent.return_value = mock_proc_parent
        mock_proc_parent.parent.return_value = mock_proc_grandparent
        mock_proc_grandparent.parent.return_value = mock_proc_root
        mock_proc_root.parent.return_value = None
        
        def get_mock_process(pid):
            if pid == my_pid:
                return mock_proc_self
            elif pid == 4000:
                return mock_proc_parent
            elif pid == 3000:
                return mock_proc_grandparent
            elif pid == 1:
                return mock_proc_root
            return MagicMock()
            
        mock_process_class.side_effect = get_mock_process
        
        engine = ActionEngine()
        
        # Assert ancestors collected (skip PID 1 on Unix)
        ancestor_pids = {pid for pid, _ in engine._sro_ancestors}
        self.assertIn(4000, ancestor_pids)
        self.assertIn(3000, ancestor_pids)
        if sys.platform != "win32":
            self.assertNotIn(1, ancestor_pids)
            
        # Verify that _is_safe_to_suspend rejects our own PID and ancestors
        proc_self = MockProcess(pid=my_pid, name="python")
        proc_parent = MockProcess(pid=4000, name="bash")
        proc_grandparent = MockProcess(pid=3000, name="launchd_mock")
        
        self.assertFalse(engine._is_safe_to_suspend(proc_self, user_whitelist_lower=set()))
        self.assertFalse(engine._is_safe_to_suspend(proc_parent, user_whitelist_lower=set()))
        self.assertFalse(engine._is_safe_to_suspend(proc_grandparent, user_whitelist_lower=set()))

    def test_notification_checks(self):
        dashboard_ok, dashboard_msg = check_dashboard_notifications()
        self.assertTrue(dashboard_ok, dashboard_msg)
        
        service_ok, service_msg = check_optimizer_service_notifications()
        self.assertTrue(service_ok, service_msg)


if __name__ == "__main__":
    print("Running SRO Safety & Notifications Validation Tests...")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSafetyAndNotifications)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n==============================")
        print("🎉 ALL VALIDATION TESTS PASSED 🎉")
        print("==============================")
        sys.exit(0)
    else:
        print("\n==============================")
        print("❌ SOME VALIDATION TESTS FAILED ❌")
        print("==============================")
        sys.exit(1)
