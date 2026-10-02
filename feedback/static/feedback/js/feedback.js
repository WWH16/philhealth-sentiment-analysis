(function () {
  var form = document.getElementById('feedbackForm');
  if (!form) return; // Submissions are paused.

  var intro = document.getElementById('formIntro');
  var done = document.getElementById('doneState');
  var btn = document.getElementById('btnSubmit');
  var btnText = btn.querySelector('.btn-text');
  var submitError = document.getElementById('submitError');
  var attempted = false;

  // Required fields carry `required`; each sits in a [id^="group-"] wrapper with an err-* message.
  var GROUPS = {
    'group-experience': 'err-experience', 'group-topics': 'err-topics',
    'group-comment': 'err-comment', 'group-consent': 'err-consent'
  };
  var topicBoxes = form.querySelectorAll('input[name="topics"]');

  function checkedTopics() {
    return [].filter.call(topicBoxes, function (box) { return box.checked; })
      .map(function (box) { return box.value; });
  }

  // Marks every missing field and returns the first invalid input, or null.
  function validate() {
    // `required` accepts whitespace; the server does not.
    commentBox.setCustomValidity(commentBox.value.trim() ? '' : 'empty');
    // At least one topic: mark the first chip so focus lands there.
    topicBoxes[0].setCustomValidity(checkedTopics().length ? '' : 'empty');
    Object.keys(GROUPS).forEach(function (group) {
      var ok = !document.getElementById(group).querySelector(':invalid');
      document.getElementById(group).classList.toggle('is-invalid', !ok);
      document.getElementById(GROUPS[group]).hidden = ok;
    });
    return form.querySelector(':invalid');
  }

  function setBusy(busy) {
    btn.disabled = busy;
    btnText.textContent = busy ? 'Sending…' : 'Submit feedback';
  }

  // The QR link works once, so the thank-you state is final.
  function showDone() {
    form.hidden = true;
    intro.hidden = true;
    done.hidden = false;
    window.scrollTo(0, 0);
    done.focus();
  }

  var commentBox = form.comments_suggestions;
  var commentCount = document.querySelector('[data-count-for="commentsSuggestions"]');
  function updateCounts() { commentCount.textContent = commentBox.value.length; }

  form.addEventListener('input', updateCounts);
  form.addEventListener('change', function () {
    submitError.hidden = true;
    if (attempted) validate();
  });

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (btn.disabled) return;

    attempted = true;
    var missing = validate();
    if (missing) {
      missing.closest('[id^="group-"]').scrollIntoView({ block: 'center' });
      missing.focus({ preventScroll: true });
      return;
    }

    setBusy(true);
    submitError.hidden = true;

    fetch(window.FEEDBACK_SUBMIT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': form.csrfmiddlewaretoken.value },
      body: JSON.stringify({
        token: form.token.value,
        experience: form.querySelector('input[name="experience"]:checked').value,
        topics: checkedTopics(),
        comment: commentBox.value.trim()
      })
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Your feedback was not sent (error ' + res.status + '). Please try again.');
          }
        });
      })
      .then(showDone)
      .catch(function (err) {
        submitError.textContent = err instanceof TypeError
          ? 'Your feedback was not sent. Check your internet connection and try again.'
          : err.message;
        submitError.hidden = false;
      })
      .then(function () { setBusy(false); });
  });

})();
