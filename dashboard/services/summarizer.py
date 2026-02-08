from transformers import pipeline

# Language detection model (supports English, French, and many others)
lang_detector = pipeline(
    "text-classification",
    model="papluca/xlm-roberta-base-language-detection",
    return_all_scores=False
)

# English summarizer (high quality)
summarizer_en = pipeline(
    "text2text-generation",  # Changed from "summarization"
    model="facebook/bart-large-cnn",
    device=-1  # CPU
)

# French summarizer (excellent for French text)
summarizer_fr = pipeline(
    "text2text-generation",  # Changed from "summarization"
    model="mrm8488/camembert2camembert_shared-finetuned-french-summarization",
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
            )[0]['generated_text']  # Changed from 'summary_text' to 'generated_text'
        else:
            # Default to English for 'en' or any other language
            summary_text = summarizer_en(
                text,
                max_length=150,
                min_length=50,
                do_sample=False
            )[0]['generated_text']  # Changed from 'summary_text' to 'generated_text'

        return summary_text.strip()

    except Exception as e:
        return f"Summary generation failed: {str(e)}"
