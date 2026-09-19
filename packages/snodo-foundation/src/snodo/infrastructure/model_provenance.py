"""What model the provider said it served.

FILE: snodo/infrastructure/model_provenance.py

Every completion record states the model that was *asked for*; the provider
also names, in the response, the model it actually served — a resolved name,
often a version, sometimes a fingerprint identifying the exact deployment.
Reading that field is provenance, not configuration: it never feeds routing,
selection or failover, and a record where served differs from requested is
something an operator reads, not something the engine acts on (a provider
serving a versioned name behind an alias is normal).

Providers differ in what they return, and some return nothing useful. An
absent or blank served name is recorded as ``None`` — not a mismatch, and
never the requested name copied in: the record must say the provider was
uninformative rather than imply nothing changed.
"""

from typing import Any, Optional


def served_model_of(response: Any) -> Optional[str]:
    """Return the model name *response* reports as served, or None.

    None means the provider said nothing usable (the field is absent, empty
    or not a string) — recorded as absence, so an uninformative provider
    never reads as a substituting one.
    """
    try:
        model = getattr(response, "model", None)
    except Exception:
        return None
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None
