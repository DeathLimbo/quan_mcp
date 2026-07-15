"""Labels: forward-looking targets. Kept separate from features to prevent
leakage (features never see t+1 close; labels only ever seen at training).
"""
from packages.labels.forward_return import (
    forward_return, forward_return_binary, ForwardReturnLabel,
)

__all__ = ["forward_return", "forward_return_binary", "ForwardReturnLabel"]
