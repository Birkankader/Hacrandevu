/* HacettepeBot — WebSocket client + DOM logic */

const API = '/api/patients';
let ws = null;
let patients = [];
let currentPatientId = null;
let selectedSubtime = null; // {date, hour, subtime}

// ─── Hasta CRUD ───

async function loadPatients() {
    const res = await fetch(API);
    patients = await res.json();
    renderPatientSelect();
}

function renderPatientSelect() {
    const sel = document.getElementById('patientSelect');
    const current = sel.value;
    sel.innerHTML = '<option value="">Hasta secin...</option>';
    patients.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.name} (${p.tc_kimlik.slice(0, 3)}****)`;
        sel.appendChild(opt);
    });
    if (current) sel.value = current;
    updateButtons();
}

function updateButtons() {
    const hasSelection = !!document.getElementById('patientSelect').value;
    document.getElementById('btnEdit').disabled = !hasSelection;
    document.getElementById('btnDelete').disabled = !hasSelection;
}

document.getElementById('patientSelect').addEventListener('change', () => {
    updateButtons();
    const newId = document.getElementById('patientSelect').value;
    if (newId !== currentPatientId) {
        currentPatientId = newId;
        if (newId) {
            checkSessionStatus(parseInt(newId));
        } else {
            updateSessionIndicator(false);
        }
    }
});

// Modal

function openPatientModal(patient = null) {
    document.getElementById('patientModal').classList.remove('hidden');
    document.getElementById('modalTitle').textContent = patient ? 'Hasta Duzenle' : 'Yeni Hasta';
    document.getElementById('editPatientId').value = patient ? patient.id : '';
    document.getElementById('inputName').value = patient ? patient.name : '';
    document.getElementById('inputTC').value = patient ? patient.tc_kimlik : '';
    document.getElementById('inputBirth').value = patient ? patient.dogum_tarihi : '';
    document.getElementById('inputPhone').value = patient ? (patient.phone || '') : '';
}

function closePatientModal() {
    document.getElementById('patientModal').classList.add('hidden');
    document.getElementById('patientForm').reset();
    document.getElementById('editPatientId').value = '';
}

async function savePatient(e) {
    e.preventDefault();
    const id = document.getElementById('editPatientId').value;
    const body = {
        name: document.getElementById('inputName').value.trim(),
        tc_kimlik: document.getElementById('inputTC').value.trim(),
        dogum_tarihi: document.getElementById('inputBirth').value.trim(),
        phone: document.getElementById('inputPhone').value.trim(),
    };

    try {
        let res;
        if (id) {
            res = await fetch(`${API}/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        } else {
            res = await fetch(API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        }

        if (!res.ok) {
            const err = await res.json();
            alert(err.detail || 'Hata olustu.');
            return;
        }

        const saved = await res.json();
        closePatientModal();
        await loadPatients();

        const sel = document.getElementById('patientSelect');
        sel.value = saved.id;
        updateButtons();
    } catch (err) {
        alert('Baglanti hatasi: ' + err.message);
    }
}

function editSelectedPatient() {
    const id = parseInt(document.getElementById('patientSelect').value);
    const patient = patients.find(p => p.id === id);
    if (patient) openPatientModal(patient);
}

async function deleteSelectedPatient() {
    const id = document.getElementById('patientSelect').value;
    if (!id) return;
    const patient = patients.find(p => p.id === parseInt(id));
    if (!confirm(`"${patient?.name}" silinecek. Emin misiniz?`)) return;

    try {
        const res = await fetch(`${API}/${id}`, { method: 'DELETE' });
        if (!res.ok) {
            alert('Silme hatasi.');
            return;
        }
        await loadPatients();
    } catch (err) {
        alert('Baglanti hatasi: ' + err.message);
    }
}

// ─── Session Management ───

function updateSessionIndicator(active, loggedIn = false) {
    const dot = document.getElementById('sessionDot');
    const text = document.getElementById('sessionText');
    if (!dot || !text) return;

    if (active && loggedIn) {
        dot.className = 'w-2.5 h-2.5 rounded-full bg-green-400 animate-pulse';
        text.textContent = 'Oturum aktif';
        text.className = 'text-sm text-green-600 font-medium';
    } else {
        dot.className = 'w-2.5 h-2.5 rounded-full bg-gray-300';
        text.textContent = 'Oturum yok';
        text.className = 'text-sm text-gray-400';
    }
}

async function checkSessionStatus(patientId) {
    if (!patientId) {
        updateSessionIndicator(false);
        return;
    }
    try {
        const res = await fetch(`/api/session/${patientId}`);
        if (res.ok) {
            const data = await res.json();
            updateSessionIndicator(data.active, data.logged_in);
        } else {
            updateSessionIndicator(false);
        }
    } catch {
        updateSessionIndicator(false);
    }
}

// ─── WebSocket Arama ───

const STEP_LABELS = {
    init: 'Baslatiliyor',
    google_visit: 'Google ziyareti',
    fill_tc: 'TC dolduruluyor',
    fill_birth: 'Dogum tarihi',
    fill_done: 'Form dolduruldu',
    recaptcha: 'reCAPTCHA',
    submit: 'Giris yapiliyor',
    search: 'Arama yapiliyor',
    selecting_type: 'Randevu tipi seciliyor',
    analyzing: 'Slotlar analiz ediliyor',
    scanning: 'Alternatif taraniyor',
    probing: 'Alt-saatler kesfediliyor',
    available: 'Musait slot bulundu',
    booking: 'Randevu aliniyor',
    result: 'Sonuc',
    retry: 'Tekrar deneniyor',
    stdout: 'Islem',
    cancel: 'Iptal',
};

let seenSteps = new Set();
let searchInProgress = false;

function ensureWsConnection() {
    return new Promise((resolve, reject) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            resolve(ws);
            return;
        }
        if (ws) {
            try { ws.close(); } catch (e) {}
            ws = null;
        }

        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${proto}//${location.host}/ws/search`);

        ws.onopen = () => resolve(ws);
        ws.onerror = () => {
            reject(new Error('WebSocket baglanti hatasi'));
            ws = null;
        };
        ws.onclose = () => {
            ws = null;
            if (searchInProgress) {
                finishSearch();
            }
        };
        ws.onmessage = handleWsMessage;
    });
}

function handleWsMessage(e) {
    const msg = JSON.parse(e.data);

    if (msg.type === 'status') {
        addProgressStep(msg.step, msg.message);
    } else if (msg.type === 'result') {
        if (msg.data && msg.data.status === 'CANCELLED') {
            addProgressStep('cancel', 'Arama iptal edildi.');
            finishSearch();
        } else {
            showResult(msg.data);
            finishSearch();
        }
    } else if (msg.type === 'error') {
        addProgressStep('error', msg.message);
        finishSearch();
    } else if (msg.type === 'session_status') {
        const data = msg.data || {};
        updateSessionIndicator(data.active, data.logged_in);
    } else if (msg.type === 'pong') {
        // keepalive
    } else if (msg.type === 'session_closed') {
        updateSessionIndicator(false);
    }
}

async function startSearch() {
    const patientId = document.getElementById('patientSelect').value;
    const searchText = document.getElementById('searchText').value.trim();

    if (!patientId) {
        alert('Lutfen bir hasta secin.');
        return;
    }

    // UI hazirla
    selectedSubtime = null;
    document.getElementById('progressSection').classList.remove('hidden');
    document.getElementById('resultSection').classList.add('hidden');
    document.getElementById('progressSteps').innerHTML = '';
    document.getElementById('progressSpinner').classList.remove('hidden');
    document.getElementById('actionButtons').classList.add('hidden');
    document.getElementById('btnCancel').classList.remove('hidden');
    resetBookButton();
    seenSteps = new Set();
    searchInProgress = true;

    try {
        await ensureWsConnection();
    } catch (err) {
        addProgressStep('error', err.message);
        finishSearch();
        return;
    }

    const randevuType = document.getElementById('randevuType').value;
    ws.send(JSON.stringify({
        action: 'search',
        patient_id: parseInt(patientId),
        search_text: searchText,
        randevu_type: randevuType,
    }));
}

async function bookSelectedSlot() {
    if (!selectedSubtime) {
        alert('Lutfen bir saat secin.');
        return;
    }

    const patientId = document.getElementById('patientSelect').value;
    const searchText = document.getElementById('searchText').value.trim();

    if (!patientId) {
        alert('Lutfen bir hasta secin.');
        return;
    }

    // UI hazirla
    document.getElementById('progressSection').classList.remove('hidden');
    document.getElementById('progressSteps').innerHTML = '';
    document.getElementById('progressSpinner').classList.remove('hidden');
    document.getElementById('actionButtons').classList.add('hidden');
    document.getElementById('btnCancel').classList.remove('hidden');
    seenSteps = new Set();
    searchInProgress = true;

    try {
        await ensureWsConnection();
    } catch (err) {
        addProgressStep('error', err.message);
        finishSearch();
        return;
    }

    const randevuType = document.getElementById('randevuType').value;
    ws.send(JSON.stringify({
        action: 'book',
        patient_id: parseInt(patientId),
        search_text: searchText,
        randevu_type: randevuType,
        book_target: selectedSubtime,
    }));
}

function addProgressStep(step, message) {
    const container = document.getElementById('progressSteps');

    const repeatableSteps = new Set(['stdout', 'scanning', 'available', 'probing', 'booking']);
    if (!repeatableSteps.has(step) && seenSteps.has(step)) {
        const existing = document.querySelector(`[data-step="${step}"]`);
        if (existing) {
            existing.querySelector('.step-msg').textContent = cleanMessage(message);
            return;
        }
    }
    seenSteps.add(step);

    const label = STEP_LABELS[step] || step;
    const isError = step === 'error';

    const div = document.createElement('div');
    div.setAttribute('data-step', step);
    div.className = `flex items-start gap-2 py-1.5 ${isError ? 'text-red-600' : 'text-gray-700'}`;
    div.innerHTML = `
        <span class="w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${isError ? 'bg-red-500' : 'bg-hacettepe'}"></span>
        <div class="min-w-0">
            <span class="text-xs font-medium uppercase tracking-wide ${isError ? 'text-red-500' : 'text-hacettepe'}">${label}</span>
            <p class="step-msg text-sm text-gray-600 break-words">${cleanMessage(message)}</p>
        </div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;

    document.getElementById('spinnerText').textContent = cleanMessage(message);
}

function cleanMessage(msg) {
    return msg.replace(/^\[[\w]+\]\s*/i, '').trim();
}

function cancelSearch() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'cancel' }));
    }
    document.getElementById('btnCancel').disabled = true;
    document.getElementById('spinnerText').textContent = 'Iptal ediliyor...';
}

function resetBookButton() {
    const btn = document.getElementById('btnBook');
    btn.disabled = true;
    btn.innerHTML = `
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        Randevu Al`;
}

function finishSearch() {
    document.getElementById('progressSpinner').classList.add('hidden');
    document.getElementById('btnCancel').classList.add('hidden');
    document.getElementById('btnCancel').disabled = false;
    document.getElementById('actionButtons').classList.remove('hidden');
    searchInProgress = false;
}

function showResult(data) {
    const section = document.getElementById('resultSection');
    section.classList.remove('hidden');

    const status = data.status || 'UNKNOWN';
    const totalAvailable = data.total_available || 0;
    const alternatives = data.alternatives || [];
    const sessionReused = data.session_reused || false;
    const probedSubtimes = data.probed_subtimes || [];
    const probedAltName = data.probed_alt_name || '';
    const booking = data.booking;

    // Header
    const icon = document.getElementById('resultIcon');
    const title = document.getElementById('resultTitle');
    const subtitle = document.getElementById('resultSubtitle');

    if (booking) {
        // Booking sonucu
        if (booking.success) {
            icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-blue-100 text-blue-600';
            icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>';
            title.textContent = 'Randevu Alindi!';
            title.className = 'text-lg font-semibold text-blue-700';
            subtitle.textContent = booking.message || '';
        } else {
            icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-yellow-100 text-yellow-600';
            icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>';
            title.textContent = 'Randevu Alinamadi';
            title.className = 'text-lg font-semibold text-yellow-700';
            subtitle.textContent = booking.message || '';
        }
    } else if (status === 'AVAILABLE') {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-green-100 text-green-600';
        icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>';
        title.textContent = 'Musait Randevu Bulundu!';
        title.className = 'text-lg font-semibold text-green-700';
        subtitle.textContent = probedSubtimes.length > 0
            ? `${probedSubtimes.reduce((s,p) => s + p.subtimes.length, 0)} alt-saat kesfedildi — asagidan secin`
            : `${totalAvailable} musait slot`;
    } else if (status === 'NOT_AVAILABLE') {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-red-100 text-red-600';
        icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';
        title.textContent = 'Uygun Randevu Bulunamadi';
        title.className = 'text-lg font-semibold text-red-700';
        subtitle.textContent = 'Tum slotlar dolu veya kapali.';
    } else if (status === 'ERROR') {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-yellow-100 text-yellow-600';
        icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01"/></svg>';
        title.textContent = 'Hata Olustu';
        title.className = 'text-lg font-semibold text-yellow-700';
        subtitle.textContent = data.error || 'Bilinmeyen hata.';
    } else {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-gray-100 text-gray-600';
        icon.innerHTML = '?';
        title.textContent = 'Durum Belirsiz';
        title.className = 'text-lg font-semibold text-gray-700';
        subtitle.textContent = 'Screenshot kontrol edin.';
    }

    if (sessionReused) {
        subtitle.textContent += ' (oturum yeniden kullanildi)';
    }

    // Content area
    const altList = document.getElementById('alternativesList');
    const slotGrid = document.getElementById('slotGrid');
    const slotSummary = document.getElementById('slotSummary');
    altList.innerHTML = '';
    slotGrid.innerHTML = '';
    slotSummary.innerHTML = '';

    // ── Alt-saat secim UI'i (probe sonuclari varsa) ──
    if (probedSubtimes.length > 0 && !booking) {
        selectedSubtime = null;
        slotGrid.classList.add('hidden');
        slotSummary.classList.add('hidden');

        // Tarihe gore grupla
        const byDate = {};
        probedSubtimes.forEach(p => {
            if (!byDate[p.date]) byDate[p.date] = [];
            p.subtimes.forEach(st => {
                byDate[p.date].push({ hour: p.hour, subtime: st });
            });
        });

        // Alt name baslik
        if (probedAltName) {
            const altHeader = document.createElement('div');
            altHeader.className = 'mb-3 p-2 bg-gray-50 rounded-lg';
            altHeader.innerHTML = `<span class="text-sm font-medium text-gray-700">${probedAltName}</span>`;
            altList.appendChild(altHeader);
        }

        Object.entries(byDate).forEach(([date, slots]) => {
            const dateCard = document.createElement('div');
            dateCard.className = 'mb-3';
            dateCard.innerHTML = `<div class="text-sm font-semibold text-gray-800 mb-2">${date}</div>`;

            const grid = document.createElement('div');
            grid.className = 'flex flex-wrap gap-2';

            slots.forEach(slot => {
                const btn = document.createElement('button');
                btn.className = 'subtime-btn px-3 py-2 rounded-lg border-2 text-sm font-medium transition '
                    + 'border-green-300 bg-green-50 text-green-800 hover:bg-green-100 hover:border-green-500';
                btn.textContent = slot.subtime;
                btn.dataset.date = date;
                btn.dataset.hour = slot.hour;
                btn.dataset.subtime = slot.subtime;

                btn.addEventListener('click', () => {
                    // Onceki secimi kaldir
                    document.querySelectorAll('.subtime-btn').forEach(b => {
                        b.classList.remove('ring-2', 'ring-blue-500', 'bg-blue-50', 'border-blue-500', 'text-blue-800');
                        b.classList.add('border-green-300', 'bg-green-50', 'text-green-800');
                    });
                    // Yeni secim
                    btn.classList.remove('border-green-300', 'bg-green-50', 'text-green-800');
                    btn.classList.add('ring-2', 'ring-blue-500', 'bg-blue-50', 'border-blue-500', 'text-blue-800');

                    selectedSubtime = {
                        date: btn.dataset.date,
                        hour: btn.dataset.hour,
                        subtime: btn.dataset.subtime,
                    };

                    // "Randevu Al" butonunu aktif et
                    const bookBtn = document.getElementById('btnBook');
                    bookBtn.disabled = false;
                    bookBtn.innerHTML = `
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                                  d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        ${btn.dataset.date} ${slot.subtime} Al`;
                });

                grid.appendChild(btn);
            });

            dateCard.appendChild(grid);
            altList.appendChild(dateCard);
        });

        // Secim bilgisi
        const infoDiv = document.createElement('div');
        infoDiv.className = 'mt-3 p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700';
        infoDiv.textContent = 'Randevu almak istediginiz saati secin, sonra "Randevu Al" butonuna tiklayin.';
        altList.appendChild(infoDiv);

    } else if (alternatives.length > 0 && !booking) {
        // Klasik alternatif listesi (probe sonucu yoksa)
        slotGrid.classList.add('hidden');
        slotSummary.classList.add('hidden');

        alternatives.forEach((alt) => {
            const appt = alt.appointments || {};
            const availSlots = appt.available_slots || [];
            const isAvail = alt.status === 'AVAILABLE' || alt.status === 'POSSIBLY_AVAILABLE';
            const borderColor = isAvail ? 'border-green-400 bg-green-50' : 'border-gray-200 bg-white';
            const badgeColor = isAvail
                ? 'bg-green-100 text-green-700'
                : (alt.status === 'NOT_AVAILABLE' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600');
            const badgeText = isAvail
                ? `${availSlots.length} musait`
                : (alt.status === 'NOT_AVAILABLE' ? 'Dolu' : 'Belirsiz');

            const byDate = {};
            availSlots.forEach(slot => {
                const date = slot.date || 'Tarih belirsiz';
                if (!byDate[date]) byDate[date] = [];
                byDate[date].push(slot.time || slot.raw || '?');
            });

            let detailHtml = '';
            if (isAvail && Object.keys(byDate).length > 0) {
                detailHtml = Object.entries(byDate).map(([date, times]) => {
                    const timeBadges = times.map(t =>
                        `<span class="inline-block bg-green-100 text-green-800 text-xs px-2 py-0.5 rounded">${t}</span>`
                    ).join(' ');
                    return `<div class="mb-1.5">
                        <span class="text-xs font-medium text-gray-700">${date}:</span>
                        <div class="flex flex-wrap gap-1 mt-0.5">${timeBadges}</div>
                    </div>`;
                }).join('');
            } else {
                detailHtml = `<p class="text-xs text-gray-500">Gorünen: ${appt.total_visible || 0} slot</p>`;
            }

            const card = document.createElement('div');
            card.className = `border rounded-lg p-3 ${borderColor} cursor-pointer transition hover:shadow-sm`;
            card.innerHTML = `
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 min-w-0">
                        <span class="w-2.5 h-2.5 rounded-full flex-shrink-0 ${isAvail ? 'bg-green-500' : 'bg-gray-400'}"></span>
                        <span class="font-medium text-sm text-gray-800 truncate">${alt.name || 'Bilinmeyen'}</span>
                    </div>
                    <div class="flex items-center gap-2 flex-shrink-0">
                        <span class="text-xs px-2 py-0.5 rounded-full ${badgeColor}">${badgeText}</span>
                        <svg class="w-4 h-4 text-gray-400 alt-chevron transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                        </svg>
                    </div>
                </div>
                <div class="alt-details hidden mt-2 pt-2 border-t border-gray-200/50">
                    ${detailHtml}
                </div>
            `;

            card.addEventListener('click', () => {
                const details = card.querySelector('.alt-details');
                const chevron = card.querySelector('.alt-chevron');
                details.classList.toggle('hidden');
                chevron.classList.toggle('rotate-180');
            });

            if (isAvail) {
                card.querySelector('.alt-details').classList.remove('hidden');
                card.querySelector('.alt-chevron').classList.add('rotate-180');
            }

            altList.appendChild(card);
        });
    }

    // Screenshot link
    const ssLink = document.getElementById('screenshotLink');
    ssLink.innerHTML = `
        <a href="/api/screenshot/last-check.png" target="_blank"
           class="text-sm text-hacettepe hover:underline inline-flex items-center gap-1">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
            </svg>
            Son screenshot'i gor
        </a>
    `;
}

// ─── Init ───
loadPatients();
