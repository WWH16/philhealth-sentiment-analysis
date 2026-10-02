import re
import joblib
from pathlib import Path
from nltk.stem import SnowballStemmer

from .models import FeedbackEntry

_ML_DIR = Path(__file__).resolve().parent / 'ml'
# Naive Bayes pipeline (TF-IDF 1-2 grams) trained on FiReCS + SentiTaglish,
# exported by training/train_sentiment_model.ipynb. Labels: 0/1/2 = neg/neu/pos.
_MODEL_PATH = _ML_DIR / 'sentiment_model.pkl'
# English + Tagalog stop words. Negation and intensity words are kept on purpose.
_STOPWORDS_PATH = _ML_DIR / 'stopwords.txt'

_model = None
_stemmer = SnowballStemmer('english')

_STOP_WORDS = frozenset()
if _STOPWORDS_PATH.exists():
    with open(_STOPWORDS_PATH, 'r', encoding='utf-8') as f:
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


def _to_sentiment(label):
    # numpy integer labels hash and compare equal to plain ints, so they hit the map as-is.
    key = label.strip().lower() if isinstance(label, str) else label
    return _LABEL_MAP.get(key, FeedbackEntry.PENDING)


def _get_model():
    global _model
    if _model is None and _MODEL_PATH.exists():
        try:
            _model = joblib.load(_MODEL_PATH)
        except Exception:
            _model = False
    return _model if _model is not False else None


def _preprocess_light(text):
    """Must match preprocess() in the training notebook exactly."""
    if not isinstance(text, str):
        return ""
    # Strip form section header prefixes (e.g., "Comments: ", "Commendation: ")
    text = re.sub(r'^(Comments|Commendation|Comments & Suggestions):\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\|\s*(Comments|Commendation|Comments & Suggestions):\s*', ' ', text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r'(.)\1{2,}', r'\1\1', text)
    text = re.sub(r'[^a-z\s]', ' ', text)
    tokens = [t for t in text.split() if t not in _STOP_WORDS]
    return " ".join(_stemmer.stem(t) for t in tokens)


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
                    res = _to_sentiment(top_class)
                    if res != FeedbackEntry.PENDING:
                        return res

        return _to_sentiment(model.predict([cleaned])[0])
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
