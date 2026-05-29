KWAME NKRUMAH UNIVERSITY OF SCIENCE AND TECHNOLOGY

Faculty of Physical and Computational Sciences

Department of Computer Science

AI Model Design, Training & Quantization

Technical Documentation — Module B

Project: Lightweight AI-Powered System Resource Optimizer

Prepared by:

Lamptey Kwaku Abednego — 3398122

Tugbah Lily Ama Mawuena — 3416522

Korli Larry Addo — 3395922

Date: May 2026

# 1. Introduction

This document details the design, mathematical formulation, training procedure, post-training quantization, and evaluation of the Gated Recurrent Unit (GRU) model used as the predictive engine of the Lightweight AI-Powered System Resource Optimizer. The model is responsible for analysing a 60-second rolling window of system telemetry and producing two outputs: a regression forecast of the next-step feature vector, and a binary confidence score estimating the probability that a resource bottleneck will occur within the next 30 seconds.

The model architecture, training configuration, and quantization pipeline are implemented across two files: `gru_model.py` defines the PyTorch neural network, and `train.py` executes the full training, evaluation, and export workflow. The final deliverable of this module is a quantized ONNX model file (`models/gru_quantized.onnx`) consumed at runtime by the inference pipeline (Module C).

# 2. Description of Gated Recurrent Units (GRU)

A **Gated Recurrent Unit (GRU)**, introduced by Cho et al. in 2014, is a specialized class of Recurrent Neural Network (RNN) architectures designed to process sequential, time-series data. In standard feed-forward neural networks, every input is treated as independent. However, system resource telemetry is inherently sequential; a sudden core CPU spike at the current second is highly correlated with the load patterns observed over the preceding seconds. 

Standard RNNs struggle to learn these long-term temporal dependencies due to the **vanishing gradient problem**, where gradients shrink exponentially during backpropagation through long time sequences, causing the network weights to stop updating. Like the Long Short-Term Memory (LSTM) network, the GRU solves this by incorporating **gating mechanisms** that regulate the flow of information. Gates are active mathematical filters that dynamically decide what information to keep, what to discard, and what to pass forward to the next hidden state.

# 3. Model Selection: Why GRU?

Three candidate architectures were evaluated against the project's primary constraint: the model must be lightweight enough to run inference at 1 Hz on a standard desktop CPU without exceeding 2% total CPU utilisation.

[TABLE]
Architecture | Parameters (approx.) | Inference Latency | Sequential Suitability | Selected
LSTM | ~120K (hidden=64, L=2) | ~8–15 ms | High — handles long dependencies | No — more parameters than needed
GRU | 44,525 (hidden=64, L=2) | ~0.9 ms | High — comparable to LSTM with fewer gates | ✅ Yes
Transformer | ~500K+ (4 heads) | ~20–50 ms | Moderate — better for long sequences | No — excessive overhead
1D-CNN | ~30K | ~2–4 ms | Low — no temporal memory | No — misses temporal dependencies
[/TABLE]

Table 3.1: Candidate architecture comparison. Parameter counts are exact for input size F=16, sequence length W=60.

The GRU was selected because it matches LSTM predictive accuracy on time-series tasks while using approximately **25% fewer parameters** due to its streamlined gate design (two gates versus three in LSTM). This significantly shrinks model size and speeds up both training and inference. It runs perfectly on local student CPU hardware, with an inference execution latency of just **0.9 ms**, satisfying the <2% CPU overhead requirement.

# 4. GRU Mathematical Formulation

A Gated Recurrent Unit processes a sequence of input vectors $x_1, x_2, ..., x_t$ (where $x_t \in \mathbb{R}^F$ represents the telemetry feature vector at second $t$) and maintains a recurrent hidden state $h_t \in \mathbb{R}^H$ that summarises the temporal history seen so far. At each timestep $t$, the GRU cell computes the following operations:

```
          Input Feature Vector x_t
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
    [Reset Gate]           [Update Gate]
     r_t (0 to 1)           z_t (0 to 1)
         │                       │
         ├───────────────────────┤
         ▼                       ▼
 [Candidate State] ──►──►──► [Final State]
     ~h_t                    h_t = (1-z_t)*h_{t-1} + z_t*~h_t
```

## 4.1 Update Gate

The update gate $z_t$ controls what fraction of the previous hidden state $h_{t-1}$ is carried forward versus replaced with the newly calculated candidate state:
$$z_t = \sigma(W_z \cdot [h_{t-1}, x_t] + b_z)$$
where $\sigma$ is the sigmoid activation function (limiting outputs strictly between 0 and 1), $W_z$ is the learnable gate weight matrix, and $b_z$ is the bias vector. An update value of $z_t \approx 1$ forces the GRU cell to retain its historical values (ideal for slow-moving metrics like temperature), whereas $z_t \approx 0$ tells the cell to replace history with the current incoming spike.

## 4.2 Reset Gate

The reset gate $r_t$ determines how much of the historical hidden state $h_{t-1}$ should be forgotten when computing the new candidate state:
$$r_t = \sigma(W_r \cdot [h_{t-1}, x_t] + b_r)$$
When $r_t \approx 0$, the cell effectively ignores its entire temporal history. This allows the model to immediately reset its hidden state when a sudden, independent event begins (e.g. a heavy compiling task launched after a long idle baseline).

## 4.3 Candidate Hidden State

The candidate hidden state $\tilde{h}_t$ combines the current input $x_t$ with the reset-gate-filtered history:
$$\tilde{h}_t = \tanh(W_h \cdot [r_t \odot h_{t-1}, x_t] + b_h)$$
where $\odot$ represents the element-wise Hadamard product, and $\tanh$ is the hyperbolic tangent activation function (bounding values to $[-1, 1]$).

## 4.4 Final Hidden State

The final hidden state $h_t$ is computed as a linear interpolation between the previous hidden state and the candidate state, dynamically balanced by the update gate:
$$h_t = (1 - z_t) \odot h_{t-1} + z_t \odot \tilde{h}_t$$
This elegant formulation enables the GRU to model both rapid short-term changes (via $\tilde{h}_t$) and long-term trends (via the carry-over history) in a single lightweight unit.

# 5. Model Architecture

The `ResourceGRU` class implements a stacked 2-layer GRU encoder followed by two independent feed-forward output heads (dual-output regression and classification heads).

[TABLE]
Layer | Type | Input Shape | Output Shape | Notes
GRU Stack | nn.GRU (2 layers) | (B, 60, F) | (B, 60, 64) | dropout=0.2 between layers
Last Hidden | Slice [:, -1, :] | (B, 60, 64) | (B, 64) | Only last timestep used
Reg FC-1 | Linear + ReLU + Dropout | (B, 64) | (B, 32) | Regression head layer 1
Reg FC-2 | Linear + Sigmoid | (B, 32) | (B, F) | Next-step forecast output
Clf FC-1 | Linear + ReLU + Dropout | (B, 64) | (B, 32) | Classification head layer 1
Clf FC-2 | Linear + Sigmoid | (B, 32) | (B, 1) | Bottleneck confidence output
[/TABLE]

Table 5.1: Layer-by-layer architecture. B = batch size, F = number of input features, hidden_size = 64.

The dual-head setup allows joint multi-task learning: the regression head predicts the raw scaled resource values at the next step to aid explainability, while the classification head directly computes the probability of a system bottleneck occurring 30 seconds ahead.

# 6. Training Configuration

## 6.1 Joint Loss Formulation

The model is trained with a composite loss that jointly optimises both heads in a single backward pass:
$$\mathcal{L}_{\text{total}} = \mathcal{L}_{\text{MSE}}(\text{reg\_out}, y_{\text{reg}}) + \mathcal{L}_{\text{BCE}}(\text{clf\_out}, y_{\text{clf}})$$
$\mathcal{L}_{\text{MSE}}$ represents the Mean Squared Error loss which penalises large forecast errors quadratically. $\mathcal{L}_{\text{BCE}}$ represents the Binary Cross-Entropy loss which is the standard loss for training binary classifiers. Equal weighting provides highly stable multi-task training.

## 6.2 Optimiser & LR Scheduler

We utilize the **Adam optimiser** (Adaptive Moment Estimation) with an initial learning rate of $10^{-3}$ and a `ReduceLROnPlateau` scheduler. The scheduler dynamically cuts the learning rate in half whenever the validation loss fails to decrease for 5 consecutive epochs, preventing oscillations. Early stopping triggers if validation loss fails to improve for 10 epochs (patience=10), at which point PyTorch automatically restores the global best-weight checkpoint. Gradient clipping (`max_norm=1.0`) is enforced to prevent exploding gradients.

[TABLE]
Hyperparameter | Value | Justification
hidden_size | 64 | Optimal capacity-overhead trade-off
num_layers | 2 | Stacked layers capture temporal hierarchies
dropout | 0.2 | Prevents overfitting on limited datasets
learning_rate | 0.001 | Standard Adam starting rate
batch_size | 64 | Efficient memory bus usage on CPU
max_epochs | 100 | Sufficient upper bound for convergence
patience | 10 | Halts training at validation minimum
window_size W | 60 samples | 60 seconds of history captures ramp-ups
label_horizon H | 30 samples | Predicts 30 seconds ahead to give action headroom
[/TABLE]

Table 6.2: Model training hyperparameters and justifications.

# 7. Post-Training INT8 Quantization

After offline PyTorch training, the weights are stored as 32-bit floats (FP32). To make the model ultra-lightweight, we export the graph to the Open Neural Network Exchange (ONNX) format and perform dynamic 8-bit integer (INT8) quantization via `onnxruntime.quantization.quantize_dynamic()`.

[TABLE]
Model Version | File | Size (MB) | Inference Latency | Accuracy Loss
FP32 baseline | gru_fp32.onnx | 0.182 MB | 1.20 ms | Baseline reference
INT8 quantized | gru_quantized.onnx | 0.175 MB | 0.90 ms | < 0.1% loss (negligible)
Size reduction | — | 3.8 % | — | —
[/TABLE]

Table 7.1: Model size and CPU inference latency before and after post-training dynamic INT8 quantization.

# 8. Evaluation Metrics and Results

## 8.1 Model Training Epoch Metrics

During offline PyTorch training on the 6,000-row augmented dataset, the early stopping mechanism successfully halted training at **Epoch 11**, restoring the global absolute minimum validation loss state from Epoch 1:

- **Validation Loss:** `0.4933` (Combined multi-task Regression MSE + Classification BCE loss)
- **Validation Accuracy:** `98.5%`
- **Validation F1-Score:** `98.6%`
- **Validation ROC-AUC:** `1.000`

## 8.2 Held-Out Test Set Evaluation

The final restored model was evaluated on the held-out 15% chronological test set (887 unseen windows), demonstrating near-perfect generalization:

[TABLE]
Metric | Head | Formula | Value (Test Set)
MAE | Regression | mean(|y_pred − y_true|) | 0.2309
RMSE | Regression | √mean((y_pred − y_true)²) | 0.3150
Accuracy | Classification | correct / total | 100.0%
F1 Score | Classification | 2 × (P × R) / (P + R) | 1.000
AUC-ROC | Classification | Area under ROC curve | 1.000
[/TABLE]

Table 8.2: Summary of the predictive model's test set performance.

[TABLE]
Confusion Matrix | Predicted Normal (0) | Predicted Bottleneck (1)
Actual Normal (0) | 554 (True Negatives) | 0 (False Positives)
Actual Bottleneck (1) | 0 (False Negatives) | 333 (True Positives)
[/TABLE]

Table 8.3: Confusion matrix on the held-out test set (887 total windows, 0 false predictions).

The Sigmoid confidence output of the classifier is passed directly to the dashboard AI progress ring as the Explainable AI metric.

# 9. References

[1] Cho, K. et al. (2014). Learning Phrase Representations using RNN Encoder-Decoder for Statistical Machine Translation. EMNLP 2014.

[2] Chung, J., Gulcehre, C., Cho, K., & Bengio, Y. (2014). Empirical Evaluation of Gated Recurrent Neural Networks on Sequence Modeling. NIPS 2014.

[3] Kingma, D. P., & Ba, J. (2015). Adam: A Method for Stochastic Optimization. ICLR 2015.

[4] Jacob, B. et al. (2018). Quantization and Training of Neural Networks for Efficient Integer-Arithmetic-Only Inference. CVPR 2018.

[5] ONNX Runtime Contributors. (2024). ONNX Runtime Quantization. https://onnxruntime.ai/

[6] Paszke, A. et al. (2019). PyTorch: An Imperative Style, High-Performance Deep Learning Library. NeurIPS 2019.
