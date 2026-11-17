from tomlkit import parse
import numpy as np

# --- Membership functions ---
def tri_mf(x, a, b, c):
    """Triangular membership function."""
    return np.maximum(np.minimum((x - a) / (b - a), (c - x) / (c - b)), 0)

def trap_mf(x, a, b, c, d):
    """Trapezoidal membership function."""
    return np.maximum(np.minimum(np.minimum((x - a) / (b - a), 1), (d - x) / (d - c)), 0)

def average_membership(mf_func, params, low, mid, high, samples=20):
    xs = np.linspace(low, high, samples)
    # Weight each x by its membership in the triangular fuzzy number
    input_weights = np.array([tri_mf(x, low, mid, high) for x in xs])
    mf_values = np.array([mf_func(x, *params) for x in xs])
    if np.sum(input_weights) == 0:
        return 0.0
    return np.sum(mf_values * input_weights) / np.sum(input_weights)

# --- Fuzzy election ---
def fuzzy_election(toml_path, inputs):
    """
    Evaluate fuzzy classification using election analogy.
    - toml_path: path to TOML file with rules
    - inputs: dict {property: value or [low, mid, high]}
    """
    # Load rules
    with open("fuzzy_rules.toml", "r", encoding="utf-8") as f:
        rules = parse(f.read())
    safety_gap = rules.get("safety", {}).get("gap", 0.0)
    
    # Initialize scores for each label
    scores = {}
    
    for ev in rules["evidence"]:
        prop = ev["property"]
        if prop not in inputs:
            continue  # Skip missing inputs
        value = inputs[prop]
        
        # Convert crisp to triple
        if not isinstance(value, (list, tuple)):
            value = [value, value, value]
        low, mid, high = value
        
        weight = ev["weight"]
        labels = ev["labels"]
        
        for fs in ev["sets"]:
            name = fs["name"]
            shape = fs["shape"]
            params = fs["params"]
            
            if shape == "tri":
                avg_mu = average_membership(tri_mf, params, low, mid, high)
            elif shape == "trap":
                avg_mu = average_membership(trap_mf, params, low, mid, high)
            else:
                raise ValueError(f"Unknown shape: {shape}")
            
            # Map fuzzy set to classification labels
            if name in labels:
                scale = labels[name]
                scores[name] = scores.get(name, 0) + avg_mu * scale * weight
    
    # Normalize scores
    total = sum(scores.values())
    if total > 0:
        for k in scores:
            scores[k] /= total
    
    # Safety gap decision
    sorted_labels = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    winner, winner_score = sorted_labels[0]
    runner_score = sorted_labels[1][1] if len(sorted_labels) > 1 else 0
    
    if winner_score - runner_score < safety_gap:
        # Conservative fallback: choose next worse in risk order
        order = ["bad", "fair", "good"]
        for lbl in order:
            if lbl != winner and lbl in scores:
                winner = lbl
                break
    
    return {"scores": scores, "winner": winner}
