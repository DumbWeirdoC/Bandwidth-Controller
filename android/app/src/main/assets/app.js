// Bandwidth Controller - Android WebView UI Logic

let currentUnit = 'MB'; // 'KB' or 'MB'
let currentKbValue = 10240; // 10 MB in KB
let targetScope = 'all'; // 'all' or 'selected'
let installedApps = [];
const selectedPackages = new Set();

const MAX_CHART_POINTS = 60;
const dlHistory = new Array(MAX_CHART_POINTS).fill(0);
const ulHistory = new Array(MAX_CHART_POINTS).fill(0);

// DOM Elements
const slider = document.getElementById('bandwidth-slider');
const displayVal = document.getElementById('display-val');
const displayUnit = document.getElementById('display-unit');
const displayAltVal = document.getElementById('display-alt-val');
const exactInput = document.getElementById('exact-number-input');
const inputAddonUnit = document.getElementById('input-addon-unit');
const btnUnitKb = document.getElementById('btn-unit-kb');
const btnUnitMb = document.getElementById('btn-unit-mb');
const scaleMin = document.getElementById('scale-min');
const scaleMid = document.getElementById('scale-mid');
const scaleMax = document.getElementById('scale-max');
const btnApply = document.getElementById('btn-apply');
const btnDisable = document.getElementById('btn-disable');
const btnStepDown = document.getElementById('btn-step-down');
const btnStepUp = document.getElementById('btn-step-up');
const presetChips = document.querySelectorAll('.preset-chip');
const trafficCanvas = document.getElementById('traffic-canvas');
const toastContainer = document.getElementById('toast-container');

// Scope & App Picker DOM
const btnScopeAll = document.getElementById('btn-scope-all');
const btnScopeSelected = document.getElementById('btn-scope-selected');
const appPickerBox = document.getElementById('app-picker-box');
const appSearchInput = document.getElementById('app-search-input');
const appListEl = document.getElementById('app-list');
const selectedCountBadge = document.getElementById('selected-count-badge');

const ctx = trafficCanvas.getContext('2d');

function updateSliderVisualFill() {
    const min = parseFloat(slider.min) || 0;
    const max = parseFloat(slider.max) || 200;
    const val = parseFloat(slider.value) || 0;
    const pct = max > min ? ((val - min) / (max - min)) * 100 : 0;
    slider.style.background = `linear-gradient(to right, #00f2fe 0%, #10b981 ${pct}%, rgba(255, 255, 255, 0.08) ${pct}%)`;
}

function updateSliderScale() {
    if (currentUnit === 'MB') {
        slider.min = '0';
        slider.max = '200';
        slider.step = '0.5';
        exactInput.min = '0';
        exactInput.max = '200';
        exactInput.step = '0.5';

        const mbVal = currentKbValue / 1024.0;
        slider.value = mbVal.toFixed(1);
        exactInput.value = mbVal.toFixed(1);

        scaleMin.textContent = '0 MB/s';
        scaleMid.textContent = '100 MB/s';
        scaleMax.textContent = '200 MB/s';

        btnUnitMb.classList.add('active');
        btnUnitKb.classList.remove('active');
        displayUnit.textContent = 'MB/s';
        inputAddonUnit.textContent = 'MB/s';
    } else {
        slider.min = '0';
        slider.max = '204800';
        slider.step = '64';
        exactInput.min = '0';
        exactInput.max = '204800';
        exactInput.step = '1';

        slider.value = Math.round(currentKbValue);
        exactInput.value = Math.round(currentKbValue);

        scaleMin.textContent = '0 KB/s';
        scaleMid.textContent = '102,400 KB/s';
        scaleMax.textContent = '204,800 KB/s (200 MB/s)';

        btnUnitKb.classList.add('active');
        btnUnitMb.classList.remove('active');
        displayUnit.textContent = 'KB/s';
        inputAddonUnit.textContent = 'KB/s';
    }
    renderReadout();
    updatePresetHighlight();
    updateSliderVisualFill();
}

function renderReadout() {
    const mbVal = currentKbValue / 1024.0;
    if (currentUnit === 'MB') {
        displayVal.textContent = mbVal < 10 ? mbVal.toFixed(2) : mbVal.toFixed(1);
        displayAltVal.textContent = `${Math.round(currentKbValue).toLocaleString()} KB/s`;
    } else {
        displayVal.textContent = Math.round(currentKbValue).toLocaleString();
        displayAltVal.textContent = `${mbVal.toFixed(2)} MB/s`;
    }
}

function updatePresetHighlight() {
    presetChips.forEach(chip => {
        const kb = parseInt(chip.getAttribute('data-kb'), 10);
        if (Math.abs(kb - currentKbValue) < 1) {
            chip.classList.add('active-preset');
        } else {
            chip.classList.remove('active-preset');
        }
    });
}

btnUnitKb.addEventListener('click', () => {
    if (currentUnit !== 'KB') {
        currentUnit = 'KB';
        updateSliderScale();
    }
});

btnUnitMb.addEventListener('click', () => {
    if (currentUnit !== 'MB') {
        currentUnit = 'MB';
        updateSliderScale();
    }
});

slider.addEventListener('input', (e) => {
    const val = parseFloat(e.target.value);
    if (currentUnit === 'MB') {
        currentKbValue = val * 1024.0;
        exactInput.value = val;
    } else {
        currentKbValue = val;
        exactInput.value = Math.round(val);
    }
    renderReadout();
    updatePresetHighlight();
    updateSliderVisualFill();
});

exactInput.addEventListener('input', (e) => {
    let val = parseFloat(e.target.value);
    if (isNaN(val)) val = 0;
    if (currentUnit === 'MB') {
        val = Math.min(Math.max(val, 0), 200);
        currentKbValue = val * 1024.0;
        slider.value = val;
    } else {
        val = Math.min(Math.max(val, 0), 204800);
        currentKbValue = val;
        slider.value = val;
    }
    renderReadout();
    updatePresetHighlight();
    updateSliderVisualFill();
});

btnStepUp.addEventListener('click', () => {
    if (currentUnit === 'MB') {
        let v = Math.min(parseFloat(slider.value) + 1, 200);
        slider.value = v;
        currentKbValue = v * 1024.0;
        exactInput.value = v;
    } else {
        let v = Math.min(parseInt(slider.value, 10) + 100, 204800);
        slider.value = v;
        currentKbValue = v;
        exactInput.value = v;
    }
    renderReadout();
    updatePresetHighlight();
    updateSliderVisualFill();
});

btnStepDown.addEventListener('click', () => {
    if (currentUnit === 'MB') {
        let v = Math.max(parseFloat(slider.value) - 1, 0);
        slider.value = v;
        currentKbValue = v * 1024.0;
        exactInput.value = v;
    } else {
        let v = Math.max(parseInt(slider.value, 10) - 100, 0);
        slider.value = v;
        currentKbValue = v;
        exactInput.value = v;
    }
    renderReadout();
    updatePresetHighlight();
    updateSliderVisualFill();
});

presetChips.forEach(chip => {
    chip.addEventListener('click', () => {
        const kb = parseInt(chip.getAttribute('data-kb'), 10);
        currentKbValue = kb;
        if (currentUnit === 'MB') {
            const mb = kb / 1024.0;
            slider.value = mb;
            exactInput.value = mb;
        } else {
            slider.value = kb;
            exactInput.value = kb;
        }
        renderReadout();
        updatePresetHighlight();
        updateSliderVisualFill();
    });
});

// --- Scope & Per-App Selection ---
function setScope(mode) {
    targetScope = mode;
    if (mode === 'all') {
        btnScopeAll.classList.add('active');
        btnScopeSelected.classList.remove('active');
        appPickerBox.style.display = 'none';
    } else {
        btnScopeSelected.classList.add('active');
        btnScopeAll.classList.remove('active');
        appPickerBox.style.display = 'flex';
        if (installedApps.length === 0) {
            loadInstalledApps();
        }
    }
}

btnScopeAll.addEventListener('click', () => setScope('all'));
btnScopeSelected.addEventListener('click', () => setScope('selected'));

function loadInstalledApps() {
    try {
        if (typeof window.Android !== 'undefined') {
            const raw = window.Android.getInstalledApps();
            installedApps = JSON.parse(raw);
            renderAppList(appSearchInput.value);
        }
    } catch (e) {
        appListEl.innerHTML = '<div class="app-list-empty">Could not load installed apps.</div>';
    }
}

function updateSelectedCount() {
    selectedCountBadge.textContent = `${selectedPackages.size} selected`;
}

function renderAppList(filterText = '') {
    const q = filterText.trim().toLowerCase();
    const filtered = installedApps.filter(app =>
        app.name.toLowerCase().includes(q) || app.package.toLowerCase().includes(q)
    );

    if (filtered.length === 0) {
        appListEl.innerHTML = '<div class="app-list-empty">No matching apps found.</div>';
        return;
    }

    appListEl.innerHTML = '';
    filtered.forEach(app => {
        const isChecked = selectedPackages.has(app.package);
        const item = document.createElement('div');
        item.className = `app-item ${isChecked ? 'selected' : ''}`;
        item.innerHTML = `
            <div class="app-item-info">
                <span class="app-item-name">${escapeHtml(app.name)}</span>
                <span class="app-item-pkg">${escapeHtml(app.package)}</span>
            </div>
            <input type="checkbox" class="app-checkbox" ${isChecked ? 'checked' : ''}>
        `;
        item.addEventListener('click', (e) => {
            const cb = item.querySelector('.app-checkbox');
            if (e.target !== cb) {
                cb.checked = !cb.checked;
            }
            if (cb.checked) {
                selectedPackages.add(app.package);
                item.classList.add('selected');
            } else {
                selectedPackages.delete(app.package);
                item.classList.remove('selected');
            }
            updateSelectedCount();
        });
        appListEl.appendChild(item);
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

appSearchInput.addEventListener('input', (e) => {
    renderAppList(e.target.value);
});

// --- Toast Notifications ---
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icon = type === 'success'
        ? '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>'
        : '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>';
    toast.innerHTML = `${icon}<span>${message}</span>`;
    toastContainer.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// --- Bridge Actions ---
function applyLimit() {
    if (targetScope === 'selected' && selectedPackages.size === 0) {
        showToast('Please select at least one app or switch to All Apps.', 'error');
        return;
    }
    try {
        if (typeof window.Android !== 'undefined') {
            const pkgsJson = JSON.stringify(Array.from(selectedPackages));
            const res = JSON.parse(window.Android.applyLimit(currentKbValue, currentUnit, targetScope, pkgsJson));
            if (res.success) {
                updateStatusUI(res.state);
                showToast(res.message || 'Bandwidth limit applied.', 'success');
            } else {
                showToast(res.message || 'Failed to apply limit.', 'error');
            }
        }
    } catch (e) {
        showToast('Error invoking Android bandwidth service.', 'error');
    }
}

function disableLimit() {
    try {
        if (typeof window.Android !== 'undefined') {
            const res = JSON.parse(window.Android.disableLimit());
            if (res.success) {
                updateStatusUI(res.state);
                showToast('Bandwidth limit removed. Full speed restored.', 'success');
            }
        }
    } catch (e) {
        showToast('Error stopping Android bandwidth service.', 'error');
    }
}

btnApply.addEventListener('click', applyLimit);
btnDisable.addEventListener('click', disableLimit);

function updateStatusUI(state) {
    const statusBadge = document.getElementById('status-badge');
    const activeText = document.getElementById('active-limit-text');
    const activeDetail = document.getElementById('active-limit-detail');
    const scopeTag = document.getElementById('active-scope-tag');

    if (state.enabled && state.limit_kb > 0) {
        statusBadge.className = 'state-badge state-active';
        statusBadge.textContent = 'Active (Throttled)';
        const valStr = state.unit === 'MB'
            ? `${state.limit_mb} MB/s (${Math.round(state.limit_kb).toLocaleString()} KB/s)`
            : `${Math.round(state.limit_kb).toLocaleString()} KB/s (${state.limit_mb} MB/s)`;
        activeText.textContent = `Throttle Active: ${valStr}`;
        activeDetail.textContent = 'Traffic shaping is active';

        if (state.scope === 'selected') {
            scopeTag.textContent = `${state.selected_count || 0} Selected Apps`;
        } else {
            scopeTag.textContent = 'All Applications';
        }
    } else {
        statusBadge.className = 'state-badge state-disabled';
        statusBadge.textContent = 'Disabled (Unlimited)';
        activeText.textContent = 'Unrestricted (Full Speed)';
        activeDetail.textContent = 'Network throughput is uncapped';
        scopeTag.textContent = targetScope === 'selected' ? `${selectedPackages.size} Selected` : 'All Applications';
    }
}

function pollTelemetry() {
    try {
        if (typeof window.Android === 'undefined') return;
        const data = JSON.parse(window.Android.getStats());
        document.getElementById('cur-download-mb').textContent = Number(data.download_mb).toFixed(2);
        document.getElementById('cur-download-kb').textContent = Number(data.download_kb).toFixed(1);
        document.getElementById('cur-upload-mb').textContent = Number(data.upload_mb).toFixed(2);
        document.getElementById('cur-upload-kb').textContent = Number(data.upload_kb).toFixed(1);

        document.getElementById('total-download-gb').textContent = `${data.total_gb_recv} GB`;
        document.getElementById('total-upload-gb').textContent = `${data.total_gb_sent} GB`;

        dlHistory.shift();
        dlHistory.push(Number(data.download_mb));
        ulHistory.shift();
        ulHistory.push(Number(data.upload_mb));
        drawWaveform();
    } catch (e) {}
}

function initStatus() {
    try {
        if (typeof window.Android === 'undefined') return;
        const state = JSON.parse(window.Android.getStatus());
        if (state.enabled && state.limit_kb > 0) {
            currentKbValue = state.limit_kb;
            currentUnit = state.unit || 'MB';
            updateSliderScale();
        }
        updateStatusUI(state);
    } catch (e) {}
}

function drawWaveform() {
    const width = trafficCanvas.width;
    const height = trafficCanvas.height;
    ctx.clearRect(0, 0, width, height);

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 1;
    for (let y = 0; y < height; y += 30) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
    }

    const maxVal = Math.max(...dlHistory, ...ulHistory, 5.0);

    function drawLine(history, strokeColor, fillColor) {
        ctx.beginPath();
        const step = width / (MAX_CHART_POINTS - 1);
        for (let i = 0; i < MAX_CHART_POINTS; i++) {
            const x = i * step;
            const norm = history[i] / maxVal;
            const y = height - (norm * (height - 15)) - 5;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 2.5;
        ctx.stroke();

        ctx.lineTo(width, height);
        ctx.lineTo(0, height);
        ctx.closePath();
        ctx.fillStyle = fillColor;
        ctx.fill();
    }

    const gradDl = ctx.createLinearGradient(0, 0, 0, height);
    gradDl.addColorStop(0, 'rgba(16, 185, 129, 0.25)');
    gradDl.addColorStop(1, 'rgba(16, 185, 129, 0.0)');
    drawLine(dlHistory, '#10b981', gradDl);

    const gradUl = ctx.createLinearGradient(0, 0, 0, height);
    gradUl.addColorStop(0, 'rgba(0, 242, 254, 0.2)');
    gradUl.addColorStop(1, 'rgba(0, 242, 254, 0.0)');
    drawLine(ulHistory, '#00f2fe', gradUl);
}

updateSliderScale();
initStatus();
setInterval(pollTelemetry, 1000);
