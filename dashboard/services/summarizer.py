from transformers import pipeline

# Language detection model (supports English, French, and many others)
lang_detector = pipeline(
    "text-classification",
    model="papluca/xlm-roberta-base-language-detection",
    return_all_scores=False
)

# English summarizer (high quality) - using legacy format
summarizer_en = pipeline(
    "summarization",
    model="facebook/bart-large-cnn",
    tokenizer="facebook/bart-large-cnn",
    device=-1  # CPU
)

# French summarizer (excellent for French text) - using legacy format
summarizer_fr = pipeline(
    "summarization",
    model="mrm8488/camembert2camembert_shared-finetuned-french-summarization",
    tokenizer="mrm8488/camembert2camembert_shared-finetuned-french-summarization",
    device=-1
)

def get_summary(text):
    """
    Detects language and generates a short summary.
    Returns a clean summary string.
    """
    if not text or len(text.strip()) < 200:
        return "Summary not available (article too short)."

    try:
        # Detect language using first 512 characters (fast & accurate)
        lang_result = lang_detector(text[:512])[0]
        detected_lang = lang_result['label'].lower()

        # Choose summarizer based on language
        if 'fr' in detected_lang:
            summary_text = summarizer_fr(
                text,
                max_length=150,
                min_length=50,
                do_sample=False
            )[0]['summary_text']
        else:
            # Default to English for 'en' or any other language
            summary_text = summarizer_en(
                text,
                max_length=150,
                min_length=50,
                do_sample=False
            )[0]['summary_text']

        return summary_text.strip()

    except Exception as e:
        return f"Summary generation failed: {str(e)}"

# Test the module
if __name__ == "__main__":
    sample = "This is a test."
    result = get_summary(sample)
    print(f"Test result: {result}")
