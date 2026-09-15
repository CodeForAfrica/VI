"""Pure strategic-intent source selection shared by inference code and tests."""


def choose_strategic_prediction(
    model_intent,
    model_confidence,
    model_available,
    llm_intent,
    llm_confidence,
    llm_available,
):
    """Choose a usable prediction without treating outages as model output."""
    if not model_available and not llm_available:
        raise RuntimeError("No strategic inference source produced a prediction")

    if model_available and not llm_available:
        return model_intent, model_confidence, "model"
    if llm_available and not model_available:
        return llm_intent, llm_confidence, "llm"

    if model_intent.lower() == llm_intent.lower():
        max_confidence = max(model_confidence, llm_confidence)
        if max_confidence >= 0.6:
            return model_intent, max_confidence, "ensemble_matched_confirmed"
        if model_confidence >= llm_confidence:
            return model_intent, model_confidence, "model_selected_after_low_match"
        return llm_intent, llm_confidence, "llm_selected_after_low_match"

    if model_confidence >= llm_confidence:
        return model_intent, model_confidence, "model"
    return llm_intent, llm_confidence, "llm"
