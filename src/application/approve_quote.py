from src.domain.quote import Quote


def decide_quote(quote: Quote, action: str) -> bool:
    normalized = action.upper()
    if normalized == "APPROVE":
        return quote.approve()
    if normalized == "REJECT":
        return quote.reject()
    raise ValueError("action must be APPROVE or REJECT")
