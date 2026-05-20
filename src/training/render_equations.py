import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def render_equation(formula_str, filename, color='#FFFFFF', size=(6.5, 0.8), fontsize=15):
    # Set up figure with transparent background
    fig = plt.figure(figsize=size, facecolor='none')
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    
    # Render LaTeX formula centered
    ax.text(0.5, 0.5, formula_str, 
            fontsize=fontsize, 
            color=color, 
            ha='center', 
            va='center',
            transform=ax.transAxes,
            math_fontfamily='dejavusans') # consistent styling
            
    # Save with high DPI and transparency
    assets_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
    os.makedirs(assets_dir, exist_ok=True)
    out_path = os.path.join(assets_dir, filename)
    
    plt.savefig(out_path, bbox_inches='tight', pad_inches=0.1, transparent=True, dpi=300)
    plt.close(fig)
    print(f"Rendered {filename} to {out_path}")

if __name__ == '__main__':
    # 1. Reset Gate Equation
    render_equation(
        r'$r_t = \sigma(W_r \cdot x_t + U_r \cdot h_{t-1} + b_r)$',
        'eq_reset_gate.png'
    )
    
    # 2. Update Gate Equation
    render_equation(
        r'$z_t = \sigma(W_z \cdot x_t + U_z \cdot h_{t-1} + b_z)$',
        'eq_update_gate.png'
    )
    
    # 3. Candidate Hidden State Equation
    render_equation(
        r'$\tilde{h}_t = \tanh(W_h \cdot x_t + U_h \cdot (r_t \odot h_{t-1}) + b_h)$',
        'eq_candidate_state.png'
    )
    
    # 4. Final Hidden State Update Equation
    render_equation(
        r'$h_t = (1 - z_t) \odot h_{t-1} + z_t \odot \tilde{h}_t$',
        'eq_final_state.png'
    )
    
    # 5. Linear Quantization Formula
    render_equation(
        r'$q = \mathrm{round}\left(\frac{x}{S}\right) + Z$',
        'eq_quantization.png'
    )
    
    # 6. Dequantization Formula
    render_equation(
        r'$x \approx S \cdot (q - Z)$',
        'eq_dequantization.png'
    )
    
    # 7. First-Order Thermal Lag Equation
    render_equation(
        r'$T_t = T_{t-1} + k \cdot (\mathrm{CPU}_t - T_{t-1}) \cdot \Delta t$',
        'eq_thermal_lag.png'
    )
    
    print("All mathematical equations rendered successfully!")
