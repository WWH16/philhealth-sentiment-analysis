import re
import joblib
from pathlib import Path
from nltk.stem import SnowballStemmer

from .models import FeedbackEntry

_ML_DIR = Path(__file__).resolve().parent / 'ml'
_MODEL_PATH = _ML_DIR / 'random_forest_sentiment.pkl'
_STOPWORDS_PATH = _ML_DIR / 'stopwords_en.txt'

_model = None
_stemmer = SnowballStemmer('english')

_STOP_WORDS = set()
if _STOPWORDS_PATH.exists():
    with open(_STOPWORDS_PATH, 'r') as f:
        _STOP_WORDS = frozenset(line.strip() for line in f if line.strip())

_LABEL_MAP = {
    0: FeedbackEntry.NEGATIVE,
    1: FeedbackEntry.NEUTRAL,
    2: FeedbackEntry.POSITIVE,
    'negative': FeedbackEntry.NEGATIVE,
    'neutral': FeedbackEntry.NEUTRAL,
    'positive': FeedbackEntry.POSITIVE,
    'irrelevant': FeedbackEntry.PENDING,
}


def _get_model():
    global _model
    if _model is None and _MODEL_PATH.exists():
        try:
            _model = joblib.load(_MODEL_PATH)
        except Exception:
            _model = False
    return _model if _model is not False else None


def _preprocess_light(text):
    if not isinstance(text, str):
        return ""
    # Strip form section header prefixes (e.g., "Comments: ", "Commendation: ")
    text = re.sub(r'^(Comments|Commendation|Comments & Suggestions):\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\|\s*(Comments|Commendation|Comments & Suggestions):\s*', ' ', text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r'[^a-z\s]', '', text)
    tokens = text.split()
    filtered_tokens = [
        _stemmer.stem(word)
        for word in tokens
        if word not in _STOP_WORDS
    ]
    return " ".join(filtered_tokens)


def analyze_comment_sentiment(comment):
    """Classify comment text only. The SQD rating is never consulted: rating
    and sentiment are separate measurements. Entries the model cannot
    classify stay PENDING for manual review."""
    if not comment or not comment.strip():
        return FeedbackEntry.NOT_APPLICABLE
    model = _get_model()
    if not model:
        return FeedbackEntry.PENDING
    cleaned = _preprocess_light(comment)
    if not cleaned:
        return FeedbackEntry.PENDING
    try:
        if hasattr(model, 'predict_proba') and hasattr(model, 'classes_'):
            probas = dict(zip(model.classes_, model.predict_proba([cleaned])[0]))
            valid_probs = {
                k: v for k, v in probas.items()
                if str(k).strip().lower() not in ('irrelevant', 'unknown')
            }
            if valid_probs:
                top_class = max(valid_probs, key=valid_probs.get)
                top_prob = valid_probs[top_class]
                if top_prob >= 0.28:
                    normalized = str(top_class).strip().lower()
                    res = _LABEL_MAP.get(normalized, FeedbackEntry.PENDING)
                    if res != FeedbackEntry.PENDING:
                        return res

        predicted_label = model.predict([cleaned])[0]
        normalized_label = (
            predicted_label.strip().lower()
            if isinstance(predicted_label, str)
            else predicted_label
        )
        return _LABEL_MAP.get(normalized_label, FeedbackEntry.PENDING)
    except Exception:
        return FeedbackEntry.PENDING


def reanalyze_pending_entries(force=False):
    qs = FeedbackEntry.objects.exclude(comment='')
    if not force:
        qs = qs.filter(sentiment=FeedbackEntry.PENDING)

    total = qs.count()
    processed = 0

    for entry in qs.iterator(chunk_size=200):
        new_sentiment = analyze_comment_sentiment(entry.comment)
        if new_sentiment != entry.sentiment:
            entry.sentiment = new_sentiment
            entry.save(update_fields=['sentiment', 'updated_at'])
            processed += 1

    return total, processed
