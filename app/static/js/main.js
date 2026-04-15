document.addEventListener('DOMContentLoaded', function () {

    // ===== SIDEBAR TOGGLE =====
    const toggleBtn = document.getElementById('sidebarToggle');
    const wrapper = document.getElementById('wrapper');

    if (toggleBtn && wrapper) {
        toggleBtn.addEventListener('click', function () {
            wrapper.classList.toggle('sidebar-hidden');
            wrapper.classList.toggle('sidebar-open');
        });
    }

    // ===== CHECKLIST FORM: YES/NO TOGGLE (legacy card-style submit.html) =====
    // The new table-based submit_all.html has its own inline script
    if (document.querySelector('.answer-btn')) {
        initChecklistForm();
    }

    // ===== PASSWORD SHOW/HIDE (custom toggle; Edge native eye only shows while focused) =====
    document.querySelectorAll('.qc-password-toggle').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            const wrap = btn.closest('.qc-password-wrap');
            if (!wrap) return;
            const input = wrap.querySelector('input');
            if (!input) return;
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
            const icon = btn.querySelector('i');
            if (icon) {
                icon.className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
            }
        });
    });

    // ===== AUTO-DISMISS ALERTS =====
    document.querySelectorAll('.alert-auto-dismiss').forEach(function (alert) {
        setTimeout(function () {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            bsAlert.close();
        }, 5000);
    });

    // ===== CLICKABLE TABLE ROWS =====
    document.querySelectorAll('tr.clickable[data-href]').forEach(function (row) {
        row.addEventListener('click', function () {
            window.location.href = this.dataset.href;
        });
    });

    // ===== SCORE RANGE DISPLAY =====
    const scoreInput = document.getElementById('scoreInput');
    const scoreDisplay = document.getElementById('scoreDisplay');
    if (scoreInput && scoreDisplay) {
        scoreDisplay.textContent = scoreInput.value;
        scoreInput.addEventListener('input', function () {
            scoreDisplay.textContent = this.value;
            updateScoreColor(parseInt(this.value));
        });
        updateScoreColor(parseInt(scoreInput.value));
    }

    function updateScoreColor(val) {
        if (!scoreDisplay) return;
        scoreDisplay.className = 'score-display fw-800 fs-3';
        if (val >= 90) scoreDisplay.style.color = '#1cc88a';
        else if (val >= 75) scoreDisplay.style.color = '#4e73df';
        else if (val >= 60) scoreDisplay.style.color = '#f6c23e';
        else scoreDisplay.style.color = '#e74a3b';
    }
});

function initChecklistForm() {
    const form = document.getElementById('checklistForm');
    if (!form) return;

    // Attach events to all answer buttons
    // Supports both data-key (combined form) and data-item-id (legacy single-template form)
    document.querySelectorAll('.answer-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const key = this.dataset.key || this.dataset.itemId;
            const val = this.dataset.value;

            // Update radio input
            const radio = this.querySelector('input[type="radio"]');
            if (radio) radio.checked = true;

            // Toggle selected state on sibling buttons
            const group = document.querySelectorAll(
                `.answer-btn[data-key="${key}"], .answer-btn[data-item-id="${key}"]`
            );
            group.forEach(b => b.classList.remove('selected'));
            this.classList.add('selected');

            // Show/hide reason container
            const reasonContainer = document.getElementById(`reason-container-${key}`);
            const reasonInput = document.getElementById(`reason-${key}`);

            if (reasonContainer && reasonInput) {
                if (val === 'no') {
                    reasonContainer.style.display = 'block';
                    reasonInput.required = true;
                    reasonInput.focus();
                } else {
                    reasonContainer.style.display = 'none';
                    reasonInput.required = false;
                    reasonInput.value = '';
                }
            }
        });
    });

    // Client-side validation before submit
    form.addEventListener('submit', function (e) {
        let valid = true;
        const itemCards = document.querySelectorAll('.checklist-item-card');

        itemCards.forEach(function (card) {
            // Support both data-key (combined form) and data-item-id (legacy)
            const key = card.dataset.key || card.dataset.itemId;
            const radios = card.querySelectorAll(`input[name="answer_${key}"]`);
            let answered = false;
            let answerVal = '';

            radios.forEach(function (r) {
                if (r.checked) {
                    answered = true;
                    answerVal = r.value;
                }
            });

            // Highlight unanswered items
            if (!answered) {
                card.style.borderColor = '#e74a3b';
                card.style.boxShadow = '0 0 0 3px rgba(231,74,59,0.15)';
                valid = false;
            } else {
                card.style.borderColor = '';
                card.style.boxShadow = '';
            }

            // Require reason for "no"
            if (answered && answerVal === 'no') {
                const reasonInput = document.getElementById(`reason-${key}`);
                if (reasonInput && reasonInput.value.trim().length < 5) {
                    reasonInput.style.borderColor = '#e74a3b';
                    valid = false;
                } else if (reasonInput) {
                    reasonInput.style.borderColor = '';
                }
            }
        });

        if (!valid) {
            e.preventDefault();
            window.scrollTo({ top: 0, behavior: 'smooth' });
            showToast('Please answer all items. Reasons are required for any "No" answers.', 'danger');
        }
    });
}

function showToast(message, type) {
    const existing = document.getElementById('jsToast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'jsToast';
    toast.className = `alert alert-${type} alert-dismissible fade show`;
    toast.style.cssText = 'position:fixed;top:70px;right:1rem;z-index:9999;max-width:400px;box-shadow:0 4px 16px rgba(0,0,0,0.15)';
    toast.innerHTML = `<i class="bi bi-exclamation-circle me-2"></i>${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
    document.body.appendChild(toast);

    setTimeout(function () {
        const bsAlert = bootstrap.Alert.getOrCreateInstance(toast);
        bsAlert.close();
    }, 6000);
}
