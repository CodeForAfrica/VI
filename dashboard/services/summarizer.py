import re

# Global variables to hold pipelines (not initialized yet)
_summarizer_en = None
_summarizer_fr = None

def _get_summarizer_en():
    """Lazy load English summarizer"""
    global _summarizer_en
    if _summarizer_en is None:
        from transformers import pipeline
        _summarizer_en = pipeline(
            "text2text-generation",
            model="facebook/bart-large-cnn",
            tokenizer="facebook/bart-large-cnn",
            device=-1  # CPU
        )
    return _summarizer_en

def _get_summarizer_fr():
    """Lazy load French summarizer"""
    global _summarizer_fr
    if _summarizer_fr is None:
        from transformers import pipeline
        _summarizer_fr = pipeline(
            "text2text-generation",
            model="mrm8488/camembert2camembert_shared-finetuned-french-summarization",
            tokenizer="mrm8488/camembert2camembert_shared-finetuned-french-summarization",
            device=-1
        )
    return _summarizer_fr

def detect_language_simple(text):
    """Simple language detection based on character patterns and common words"""
    text_lower = text.lower()[:512]  # First 512 chars for speed
    
    # Common French words
    french_words = ['le', 'la', 'les', 'des', 'du', 'de', 'et', 'est', 'que', 'qui', 'ce', 'se', 'ne', 'pas', 'dans', 'pour', 'avec', 'sur', 'par']
    french_count = sum(1 for word in french_words if f' {word} ' in f' {text_lower} ')
    
    # If more than 3 French indicators, assume French
    if french_count > 3:
        return 'fr'
    
    # Otherwise assume English (or other languages)
    return 'en'

def get_summary(text):
    """
    Detects language and generates a short summary.
    Returns a clean summary string.
    """
    if not text or len(text.strip()) < 200:
        return "Summary not available (article too short)."

    try:
        # Simple language detection (avoids problematic papluca model)
        detected_lang = detect_language_simple(text)
        
        # Load appropriate summarizer based on language
        if detected_lang == 'fr':
            summarizer = _get_summarizer_fr()
            summary_text = summarizer(
                text,
                max_length=150,
                min_length=50,
                do_sample=False
            )[0]['generated_text']  # Use 'generated_text' for text2text-generation
        else:
            # Default to English for 'en' or any other language
            summarizer = _get_summarizer_en()
            summary_text = summarizer(
                text,
                max_length=150,
                min_length=50,
                do_sample=False
            )[0]['generated_text']  # Use 'generated_text' for text2text-generation

        return summary_text.strip()

    except Exception as e:
        return f"Summary generation failed: {str(e)}"

# Test the module
if __name__ == "__main__":
    sample = "This is a test."
    result = get_summary(sample)
    print(f"Test result: {result}")
