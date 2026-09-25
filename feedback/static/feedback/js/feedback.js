function getCookie(name) {
  var value = '; ' + document.cookie;
  var parts = value.split('; ' + name + '=');
  if (parts.length === 2) return parts.pop().split(';').shift();
  return '';
}

function updateDateTime() {
  var dtInput = document.getElementById('dateTime');
  if (dtInput && !dtInput.value) {
    var now = new Date();
    var year = now.getFullYear();
    var month = String(now.getMonth() + 1).padStart(2, '0');
    var day = String(now.getDate()).padStart(2, '0');
    var hours = String(now.getHours()).padStart(2, '0');
    var mins = String(now.getMinutes()).padStart(2, '0');
    dtInput.value = year + '-' + month + '-' + day + 'T' + hours + ':' + mins;
  }
}

function handleCC1Change(optionValue) {
  if (optionValue === 4) {
    // If option 4 (do not know CC), specification mandates answering "N/A" on CC2 (value 5) and CC3 (value 4)
    var cc2NA = document.querySelector('input[name="cc2"][value="5"]');
    if (cc2NA) cc2NA.checked = true;

    var cc3NA = document.querySelector('input[name="cc3"][value="4"]');
    if (cc3NA) cc3NA.checked = true;

    clearFieldErrorByGroupId('cc2Block');
    clearFieldErrorByGroupId('cc3Block');
  }
}

/* ── SUBMIT VALIDATION UX ────────────────────────────────────────────────
   Native `required` validation is unreliable here: radio/checkbox groups
   are scattered across a horizontally-scrolling table (SQD matrix) and a
   long paper-form layout, so the browser's default "jump to first invalid
   field" often leaves the field off-screen or mid-scroll. This builds an
   explicit list of required fields, highlights every one that's missing
   (not just the first), shows a summary banner naming each, and scrolls/
   focuses the first offender - including handling the table's horizontal
   scroll for the SQD rows. */

var REQUIRED_FIELDS = [
  { kind: 'input', id: 'dateTime', label: 'Date & Time' },
  { kind: 'input', id: 'contactNo', label: 'Contact No.' },
  { kind: 'input', id: 'emailAddress', label: 'Email Address' },
  { kind: 'input', id: 'clientAge', label: 'Age' },
  { kind: 'radio', name: 'client_type', groupId: 'group-clientType', label: 'Client Type' },
  { kind: 'radio', name: 'sex', groupId: 'group-sex', label: 'Sex' },
  { kind: 'select', id: 'staffAssisted', groupId: 'group-staffAssisted', label: 'Staff Who Assisted You' },
  { kind: 'radio', name: 'cc1', groupId: 'cc1Block', label: 'CC1 Answer' },
  { kind: 'radio', name: 'cc2', groupId: 'cc2Block', label: 'CC2 Answer' },
  { kind: 'radio', name: 'cc3', groupId: 'cc3Block', label: 'CC3 Answer' },
  { kind: 'radio', name: 'sqd0', groupId: 'sqdRow-sqd0', label: 'SQD0. Overall Satisfaction', isRow: true },
  { kind: 'radio', name: 'sqd1', groupId: 'sqdRow-sqd1', label: 'SQD1. Responsiveness', isRow: true },
  { kind: 'radio', name: 'sqd2', groupId: 'sqdRow-sqd2', label: 'SQD2. Reliability', isRow: true },
  { kind: 'radio', name: 'sqd3', groupId: 'sqdRow-sqd3', label: 'SQD3. Access & Facility', isRow: true },
  { kind: 'radio', name: 'sqd4', groupId: 'sqdRow-sqd4', label: 'SQD4. Communication', isRow: true },
  { kind: 'radio', name: 'sqd5', groupId: 'sqdRow-sqd5', label: 'SQD5. Costs', isRow: true },
  { kind: 'radio', name: 'sqd6', groupId: 'sqdRow-sqd6', label: 'SQD6. Integrity', isRow: true },
  { kind: 'radio', name: 'sqd7', groupId: 'sqdRow-sqd7', label: 'SQD7. Assurance', isRow: true },
  { kind: 'radio', name: 'sqd8', groupId: 'sqdRow-sqd8', label: 'SQD8. Outcome', isRow: true },
  { kind: 'checkbox', id: 'privacyConsent', groupId: 'group-privacyConsent', label: 'Privacy Consent' }
];

var validationAttempted = false;

function fieldGroupEl(field) {
  if (field.groupId) {
    var grp = document.getElementById(field.groupId);
    if (grp) return grp;
  }
  if (field.kind === 'input' || field.kind === 'select') {
    var input = document.getElementById(field.id);
    return input ? input.parentElement : null;
  }
  return null;
}

function fieldFocusEl(field) {
  if (field.kind === 'input' || field.kind === 'select') return document.getElementById(field.id);
  if (field.kind === 'checkbox') return document.getElementById(field.id);
  var checked = document.querySelector('input[name="' + field.name + '"]:checked');
  if (checked) return checked;
  return document.querySelector('input[name="' + field.name + '"]');
}

function isFieldValid(field) {
  if (field.kind === 'input') {
    var input = document.getElementById(field.id);
    return input ? input.checkValidity() : true;
  }
  if (field.kind === 'select') {
    var select = document.getElementById(field.id);
    return select ? (select.disabled || (!!select.value && select.checkValidity())) : true;
  }
  if (field.kind === 'checkbox') {
    var box = document.getElementById(field.id);
    return box ? box.checked : true;
  }
  return !!document.querySelector('input[name="' + field.name + '"]:checked');
}

function ensureErrorMsgEl(field, groupEl) {
  if (!groupEl) return null;
  var msg = groupEl.querySelector('.field-error-msg[data-field="' + (field.id || field.name) + '"]');
  if (msg) return msg;

  msg = document.createElement('p');
  msg.className = 'field-error-msg';
  msg.setAttribute('data-field', field.id || field.name);
  msg.setAttribute('role', 'alert');
  msg.setAttribute('aria-live', 'polite');
  msg.hidden = true;
  msg.innerHTML = '<span class="material-icons-round" aria-hidden="true">error_outline</span><span class="field-error-text"></span>';

  if (field.isRow) {
    // Append inside the row's first cell so it stays visible within the
    // horizontally-scrolling table instead of breaking the row layout.
    var firstCell = groupEl.querySelector('td');
    if (firstCell) firstCell.appendChild(msg);
  } else {
    groupEl.appendChild(msg);
  }
  return msg;
}

function setFieldInvalid(field, invalid) {
  var groupEl = fieldGroupEl(field);
  if (!groupEl) return;

  var msg = ensureErrorMsgEl(field, groupEl);

  if (field.kind === 'input' || field.kind === 'select') {
    var input = document.getElementById(field.id);
    if (input) {
      input.classList.toggle('field-invalid-input', invalid);
      input.setAttribute('aria-invalid', invalid ? 'true' : 'false');
    }
  } else if (field.isRow) {
    groupEl.classList.toggle('field-invalid-row', invalid);
  } else {
    groupEl.classList.toggle('field-invalid-group', invalid);
  }

  if (msg) {
    var textEl = msg.querySelector('.field-error-text');
    if (textEl) {
      textEl.textContent = field.kind === 'checkbox'
        ? 'Please check this box to continue.'
        : (field.kind === 'select'
            ? 'Please select the staff member who assisted you.'
            : (field.kind === 'radio' ? 'Please select one option.' : 'This field is required.'));
    }
    msg.hidden = !invalid;
  }
}

function clearFieldErrorByGroupId(groupId) {
  var field = REQUIRED_FIELDS.filter(function (f) { return f.groupId === groupId; })[0];
  if (field) setFieldInvalid(field, false);
}

function scrollToField(field) {
  var groupEl = fieldGroupEl(field);
  var focusEl = fieldFocusEl(field);
  var target = groupEl || focusEl;
  if (target && target.scrollIntoView) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
  }
  if (focusEl && focusEl.focus) {
    setTimeout(function () { focusEl.focus({ preventScroll: true }); }, 260);
  }
}

function validateCSMForm(opts) {
  var silent = opts && opts.onlyIfAttempted && !validationAttempted;
  var invalidFields = [];

  REQUIRED_FIELDS.forEach(function (field) {
    var valid = isFieldValid(field);
    if (!silent) setFieldInvalid(field, !valid);
    if (!valid) invalidFields.push(field);
  });

  return invalidFields;
}

function handleSubmitCSM(event) {
  if (event) event.preventDefault();

  validationAttempted = true;
  var invalidFields = validateCSMForm();

  if (invalidFields.length > 0) {
    scrollToField(invalidFields[0]);
    return;
  }

  var btnSubmit = document.getElementById('btnSubmitCSM');
  if (btnSubmit && btnSubmit.disabled) {
    return; // Hardened against double-click double submission
  }

  if (btnSubmit) {
    btnSubmit.disabled = true;
    btnSubmit.classList.add('is-loading');
  }

  var servicesAvailed = Array.from(document.querySelectorAll('input[name="services_availed"]:checked'))
    .map(function (cb) { return cb.value; });

  var staffSelect = document.getElementById('staffAssisted');
  var staffAssistedVal = staffSelect && staffSelect.value ? staffSelect.value : null;
  var staffNameVal = '';
  if (staffSelect && staffSelect.selectedIndex > 0) {
    staffNameVal = (staffSelect.options[staffSelect.selectedIndex].text || '').trim();
  }

  var payload = {
    date_time: (document.getElementById('dateTime') || {}).value || null,
    contact_no: (document.getElementById('contactNo') || {}).value || '',
    email_address: (document.getElementById('emailAddress') || {}).value || '',
    age: (document.getElementById('clientAge') || {}).value || null,
    client_type: (document.querySelector('input[name="client_type"]:checked') || {}).value || '',
    sex: (document.querySelector('input[name="sex"]:checked') || {}).value || '',
    name_of_client: (document.getElementById('clientName') || {}).value || '',
    staff_assisted: staffAssistedVal,
    staff_name: staffNameVal,
    services_availed: servicesAvailed,
    cc1: (document.querySelector('input[name="cc1"]:checked') || {}).value || '',
    cc2: (document.querySelector('input[name="cc2"]:checked') || {}).value || '',
    cc3: (document.querySelector('input[name="cc3"]:checked') || {}).value || '',
    sqd0: (document.querySelector('input[name="sqd0"]:checked') || {}).value || null,
    sqd1: (document.querySelector('input[name="sqd1"]:checked') || {}).value || null,
    sqd2: (document.querySelector('input[name="sqd2"]:checked') || {}).value || null,
    sqd3: (document.querySelector('input[name="sqd3"]:checked') || {}).value || null,
    sqd4: (document.querySelector('input[name="sqd4"]:checked') || {}).value || null,
    sqd5: (document.querySelector('input[name="sqd5"]:checked') || {}).value || null,
    sqd6: (document.querySelector('input[name="sqd6"]:checked') || {}).value || null,
    sqd7: (document.querySelector('input[name="sqd7"]:checked') || {}).value || null,
    sqd8: (document.querySelector('input[name="sqd8"]:checked') || {}).value || null,
    comments_suggestions: (document.getElementById('commentsSuggestions') || {}).value || '',
    commendation: (document.getElementById('commendation') || {}).value || ''
  };

  var submitUrl = window.FEEDBACK_SUBMIT_URL || '/submit/';
  fetch(submitUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken')
    },
    body: JSON.stringify(payload)
  })
  .then(function (res) {
    if (!res.ok) {
      return res.json().then(function (errData) { throw new Error(errData.error || 'Server error (' + res.status + ')'); });
    }
    return res.json();
  })
  .then(function (data) {
    if (btnSubmit) {
      btnSubmit.disabled = false;
      btnSubmit.classList.remove('is-loading');
    }
    if (data.ok) {
      var sw = document.getElementById('successWrap');
      if (sw) sw.classList.add('is-open');
      document.body.style.overflow = 'hidden';
      startSuccessTimer();
    }
  })
  .catch(function (err) {
    if (btnSubmit) {
      btnSubmit.disabled = false;
      btnSubmit.classList.remove('is-loading');
    }
    // Show inline error banner instead of alert() — keeps context, matches the form's error pattern
    var existingBanner = document.getElementById('submitErrorBanner');
    if (existingBanner) existingBanner.remove();
    var banner = document.createElement('p');
    banner.id = 'submitErrorBanner';
    banner.setAttribute('role', 'alert');
    banner.className = 'field-error-msg';
    banner.style.cssText = 'justify-content:center; padding:10px 14px; background:#fef2f2; border:1.5px solid #dc2626; border-radius:2px; font-size:12px; margin-top:8px;';
    banner.innerHTML = '<span class="material-icons-round" aria-hidden="true" style="font-size:16px;">error_outline</span><span>' + (err.message || 'Network error. Please check your connection and try again.') + '</span>';
    var footer = document.getElementById('btnSubmitCSM');
    if (footer && footer.parentNode) footer.parentNode.appendChild(banner);
  });
}

var successTimer = null;
var successCountdown = 5;

function updateResetBtnText() {
  var textEl = document.getElementById('resetBtnText');
  if (textEl) {
    textEl.textContent = 'Done';
  }
}

function startSuccessTimer() {
  stopSuccessTimer();
  successCountdown = 5;
  updateResetBtnText();
  
  successTimer = setInterval(function () {
    successCountdown--;
    if (successCountdown <= 0) {
      stopSuccessTimer();
      resetCSMForm();
    } else {
      updateResetBtnText();
    }
  }, 1000);
}

function stopSuccessTimer() {
  if (successTimer) {
    clearInterval(successTimer);
    successTimer = null;
  }
}

function resetCSMForm() {
  stopSuccessTimer();

  var form = document.getElementById('csmForm');
  if (form) form.reset();
  var staffSel = document.getElementById('staffAssisted');
  if (staffSel) staffSel.selectedIndex = 0;

  updateDateTime();

  validationAttempted = false;
  REQUIRED_FIELDS.forEach(function (field) { setFieldInvalid(field, false); });

  var sw = document.getElementById('successWrap');
  if (sw) sw.classList.remove('is-open');

  document.body.style.overflow = '';
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function handleBackdropClick(event) {
  resetCSMForm();
}

document.addEventListener('DOMContentLoaded', function () {
  updateDateTime();

  var form = document.getElementById('csmForm');
  if (form) {
    // Debounced live validation — clears field errors as the user fixes them,
    // but batches rapid input events to avoid layout thrashing on every keystroke.
    var validationTimer = null;
    function debouncedValidate() {
      clearTimeout(validationTimer);
      validationTimer = setTimeout(function () {
        validateCSMForm({ onlyIfAttempted: true });
      }, 120);
    }

    form.addEventListener('input', function () {
      // Also clear any stale submit-error banner when user starts correcting
      var banner = document.getElementById('submitErrorBanner');
      if (banner) banner.remove();
      debouncedValidate();
    });
    form.addEventListener('change', function () {
      var banner = document.getElementById('submitErrorBanner');
      if (banner) banner.remove();
      debouncedValidate();
    });
  }
});

document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') {
    var sw = document.getElementById('successWrap');
    if (sw && sw.classList.contains('is-open')) {
      resetCSMForm();
    }
  }
});
