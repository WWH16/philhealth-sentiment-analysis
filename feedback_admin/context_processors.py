from feedback.models import FeedbackEntry

def feedback_stats(request):
    """Provides global stats like total feedback count to all templates."""
    if request.path.startswith('/dashboard/'):
        return {
            'global_feedback_count': FeedbackEntry.objects.count()
        }
    return {}
