(function () {
  var form = document.getElementById('feedbackForm');
  if (!form) return; // Submissions are paused.

  var intro = document.getElementById('formIntro');
  var done = document.getElementById('doneState');
  var btn = document.getElementById('btnSubmit');
  var btnText = btn.querySelector('.btn-text');
  var submitError = document.getElementById('submitError');
  var resetTimer = null;
  var attempted = false;

  var REQUIRED = [
    {
      group: 'group-experience',
      error: 'err-experience',
      isValid: function () { return !!form.querySelector('input[name="experience"]:checked'); },
      focusEl: function () { return form.querySelector('input[name="experience"]'); }
    },
    {
      group: 'group-consent',
      error: 'err-consent',
      isValid: function () { return form.privacyConsent.checked; },
      focusEl: function () { return form.privacyConsent; }
    }
  ];

  function getCookie(name) {
    var parts = ('; ' + document.cookie).split('; ' + name + '=');
    return parts.length === 2 ? parts.pop().split(';').shift() : '';
  }

  // Marks every missing field and returns the first one, or null.
  function validate() {
    var first = null;
    REQUIRED.forEach(function (field) {
      var ok = field.isValid();
      document.getElementById(field.group).classList.toggle('is-invalid', !ok);
      document.getElementById(field.error).hidden = ok;
      if (!ok && !first) first = field;
    });
    return first;
  }

  function clearErrors() {
    REQUIRED.forEach(function (field) {
      document.getElementById(field.group).classList.remove('is-invalid');
      document.getElementById(field.error).hidden = true;
    });
    submitError.hidden = true;
  }

  function setBusy(busy) {
    btn.disabled = busy;
    btnText.textContent = busy ? 'Sending…' : 'Submit feedback';
  }

  function showDone() {
    form.hidden = true;
    intro.hidden = true;
    done.hidden = false;
    window.scrollTo(0, 0);
    done.focus();
    resetTimer = setTimeout(resetForm, 5000);
  }

  function resetForm() {
    clearTimeout(resetTimer);
    form.reset();
    attempted = false;
    clearErrors();
    updateCounts();
    done.hidden = true;
    intro.hidden = false;
    form.hidden = false;
    window.scrollTo(0, 0);
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
      document.getElementById(missing.group).scrollIntoView({ block: 'center' });
      missing.focusEl().focus({ preventScroll: true });
      return;
    }

    setBusy(true);
    submitError.hidden = true;

    fetch(window.FEEDBACK_SUBMIT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify({
        experience: form.querySelector('input[name="experience"]:checked').value,
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

  document.getElementById('btnDone').addEventListener('click', resetForm);
})();
