/* HacettepeBot — WebSocket client + DOM logic */

const API = '/api/patients';
let ws = null;
let patients = [];
let currentPatientId = null;

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

        // Yeni veya guncellenen hastayi sec
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
    available: 'Musait slot bulundu',
    result: 'Sonuc',
    retry: 'Tekrar deneniyor',
    stdout: 'Islem',
};

let seenSteps = new Set();
let searchInProgress = false;

function ensureWsConnection() {
    return new Promise((resolve, reject) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            resolve(ws);
            return;
        }
        // Eski WS varsa kapat
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
        showResult(msg.data);
        finishSearch();
    } else if (msg.type === 'error') {
        addProgressStep('error', msg.message);
        finishSearch();
    } else if (msg.type === 'session_status') {
        const data = msg.data || {};
        updateSessionIndicator(data.active, data.logged_in);
    } else if (msg.type === 'pong') {
        // keepalive response, ignore
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
    document.getElementById('progressSection').classList.remove('hidden');
    document.getElementById('resultSection').classList.add('hidden');
    document.getElementById('progressSteps').innerHTML = '';
    document.getElementById('progressSpinner').classList.remove('hidden');
    document.getElementById('btnSearch').disabled = true;
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

function addProgressStep(step, message) {
    const container = document.getElementById('progressSteps');

    // Ayni step icin guncelle (stdout, scanning, available haric — bunlar tekrarlanabilir)
    const repeatableSteps = new Set(['stdout', 'scanning', 'available']);
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

    // Spinner text guncelle
    document.getElementById('spinnerText').textContent = cleanMessage(message);
}

function cleanMessage(msg) {
    // [BILGI] [UYARI] gibi etiketleri kaldir
    return msg.replace(/^\[[\w]+\]\s*/i, '').trim();
}

function finishSearch() {
    document.getElementById('progressSpinner').classList.add('hidden');
    document.getElementById('btnSearch').disabled = false;
    searchInProgress = false;
    // WS'yi kapatma — session persistence icin acik tut
}

function showResult(data) {
    const section = document.getElementById('resultSection');
    section.classList.remove('hidden');

    const status = data.status || 'UNKNOWN';
    const totalAvailable = data.total_available || 0;
    const totalVisible = data.total_visible || 0;
    const alternatives = data.alternatives || [];
    const sessionReused = data.session_reused || false;
    // Geriye uyumluluk: eski format destegi
    const slots = data.slots || { green: 0, red: 0, grey: 0, total: 0, details: [] };

    // Header
    const icon = document.getElementById('resultIcon');
    const title = document.getElementById('resultTitle');
    const subtitle = document.getElementById('resultSubtitle');

    if (status === 'AVAILABLE' || status === 'POSSIBLY_AVAILABLE') {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-green-100 text-green-600';
        icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>';
        const availCount = alternatives.filter(a => a.status === 'AVAILABLE').length;
        title.textContent = 'Musait Randevu Bulundu!';
        title.className = 'text-lg font-semibold text-green-700';
        subtitle.textContent = alternatives.length > 1
            ? `${availCount}/${alternatives.length} alternatifde musait randevu (toplam ${totalAvailable} slot)`
            : `${totalAvailable} musait slot`;
    } else if (status === 'NOT_AVAILABLE') {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-red-100 text-red-600';
        icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';
        title.textContent = 'Uygun Randevu Bulunamadi';
        title.className = 'text-lg font-semibold text-red-700';
        subtitle.textContent = alternatives.length > 1
            ? `${alternatives.length} alternatif tarandi, hicbirinde musait slot yok.`
            : 'Tum slotlar dolu veya kapali.';
    } else if (status === 'ERROR') {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-yellow-100 text-yellow-600';
        icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>';
        title.textContent = 'Hata Olustu';
        title.className = 'text-lg font-semibold text-yellow-700';
        subtitle.textContent = data.error || 'Bilinmeyen hata.';
    } else {
        icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-gray-100 text-gray-600';
        icon.innerHTML = '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>';
        title.textContent = 'Durum Belirsiz';
        title.className = 'text-lg font-semibold text-gray-700';
        subtitle.textContent = 'Screenshot kontrol edin.';
    }

    // Session reuse badge
    if (sessionReused) {
        subtitle.textContent += ' (oturum yeniden kullanildi)';
    }

    // Alternatives list
    const altList = document.getElementById('alternativesList');
    const slotGrid = document.getElementById('slotGrid');
    const slotSummary = document.getElementById('slotSummary');
    altList.innerHTML = '';
    slotGrid.innerHTML = '';
    slotSummary.innerHTML = '';

    if (alternatives.length > 0) {
        // Her alternatif icin kart goster
        slotGrid.classList.add('hidden');
        slotSummary.classList.add('hidden');

        alternatives.forEach((alt, idx) => {
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

            // Slotlari tarihe gore grupla
            const byDate = {};
            availSlots.forEach(slot => {
                const date = slot.date || 'Tarih belirsiz';
                if (!byDate[date]) byDate[date] = [];
                byDate[date].push(slot.time || slot.raw || '?');
            });

            // Detay HTML olustur
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
            } else if (alt.formatted) {
                detailHtml = `<p class="text-xs text-gray-500">${alt.formatted}</p>`;
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

            // Toggle detaylar
            card.addEventListener('click', () => {
                const details = card.querySelector('.alt-details');
                const chevron = card.querySelector('.alt-chevron');
                details.classList.toggle('hidden');
                chevron.classList.toggle('rotate-180');
            });

            // Musait olanlari varsayilan olarak ac
            if (isAvail) {
                card.querySelector('.alt-details').classList.remove('hidden');
                card.querySelector('.alt-chevron').classList.add('rotate-180');
            }

            altList.appendChild(card);
        });
    } else {
        // Tek sonuc — eski gorunum (geriye uyumluluk)
        slotGrid.classList.remove('hidden');
        slotSummary.classList.remove('hidden');

        const details = slots.details || [];
        details.forEach(slot => {
            const div = document.createElement('div');
            const colorClasses = {
                green: 'bg-green-50 border-green-300 text-green-800',
                red: 'bg-red-50 border-red-300 text-red-800',
                grey: 'bg-gray-100 border-gray-300 text-gray-600',
                unknown: 'bg-yellow-50 border-yellow-300 text-yellow-800',
            };
            div.className = `rounded-lg border p-2 text-center text-sm ${colorClasses[slot.color] || colorClasses.unknown}`;
            div.textContent = slot.time || '—';
            slotGrid.appendChild(div);
        });

        if (slots.total > 0) {
            [
                { label: 'Musait', count: slots.green, color: 'text-green-600' },
                { label: 'Dolu', count: slots.red, color: 'text-red-600' },
                { label: 'Kapali', count: slots.grey, color: 'text-gray-500' },
            ].forEach(item => {
                if (item.count > 0) {
                    const span = document.createElement('span');
                    span.className = `font-medium ${item.color}`;
                    span.textContent = `${item.label}: ${item.count}`;
                    slotSummary.appendChild(span);
                }
            });
            const total = document.createElement('span');
            total.className = 'text-gray-400';
            total.textContent = `Toplam: ${slots.total}`;
            slotSummary.appendChild(total);
        }
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
