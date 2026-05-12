# =============================================================================
# gru_model.py — Quantized GRU with dual output heads
# KNUST Final Year Project — Group 4
# =============================================================================

import torch
import torch.nn as nn


class ResourceGRU(nn.Module):
    """
    Dual-output GRU for system resource forecasting.

    Inputs
    ------
    x : Tensor, shape (batch, seq_len, n_features)

    Outputs
    -------
    reg_out  : Tensor, shape (batch, n_features)  — next-step feature forecast
    conf_out : Tensor, shape (batch, 1)            — bottleneck probability (sigmoid)
    """

    def __init__(self, n_features: int, hidden_size: int = 64,
                 num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers  = num_layers

        self.gru = nn.GRU(
            input_size   = n_features,
            hidden_size  = hidden_size,
            num_layers   = num_layers,
            batch_first  = True,
            dropout      = dropout if num_layers > 1 else 0.0,
        )

        # ── Regression head: predict next-step feature vector ────────────────
        self.reg_head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, n_features),
            nn.Sigmoid(),          # outputs stay in [0,1] (normalised space)
        )

        # ── Classification head: bottleneck probability ───────────────────────
        self.clf_head = nn.Sequential(
            nn.Linear(hidden_size, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor):
        # x: (B, W, F)
        out, _ = self.gru(x)          # out: (B, W, H)
        last   = out[:, -1, :]        # last hidden state: (B, H)
        return self.reg_head(last), self.clf_head(last)

    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)
