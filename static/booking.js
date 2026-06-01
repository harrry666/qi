const SLUG = document.body.dataset.slug;
const DAY_LETTERS = ['M','T','W','T','F','S','S'];
const MONTHS = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
const ICONS = ['✂️','💇','💈','💅','🧖','💆','🪮'];

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

let state = {
  services: [],
  selected: { service: null, date: null, time: null, comment: '' },
  weekStart: null,
};

function icon(i) { return ICONS[i % ICONS.length]; }

function getMonday(d) {
  const date = new Date(d);
  const day = date.getDay();
  date.setDate(date.getDate() - (day === 0 ? 6 : day - 1));
  date.setHours(0,0,0,0);
  return date;
}

function fmtDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function fmtDisplay(dateStr, time) {
  const [y,m,d] = dateStr.split('-').map(Number);
  const days = ['周日','周一','周二','周三','周四','周五','周六'];
  const dt = new Date(y, m-1, d);
  return `${MONTHS[m-1]}${d}日 ${days[dt.getDay()]}  ${time}`;
}

function fmtDuration(mins) {
  if (mins < 60) return `${mins}分钟`;
  const h = Math.floor(mins/60), m = mins % 60;
  return m ? `${h}小时${m}分钟` : `${h}小时`;
}

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ── Services ─────────────────────────────────────────────────────────────────

async function loadServices() {
  const res = await fetch(`/api/book/${SLUG}/services`);
  state.services = await res.json();
  renderServices();
}

function renderServices() {
  const list = document.getElementById('service-list');
  if (!state.services.length) {
    list.innerHTML = '<p style="color:var(--muted);padding:20px 0">暂无可用服务。</p>';
    return;
  }
  list.innerHTML = state.services.map((s, i) => `
    <div class="service-item" onclick="selectService(${s.id})">
      <div class="svc-icon">${s.emoji ? esc(s.emoji) : icon(i)}</div>
      <div>
        <div class="svc-name">${esc(s.name)}</div>
        ${s.name_sub ? `<div class="svc-name-sub">${esc(s.name_sub)}</div>` : ''}
      </div>
      <div class="svc-meta">
        <div class="svc-duration">${fmtDuration(s.duration_mins)}</div>
        ${s.price ? `<div class="svc-price">$${s.price % 1 === 0 ? s.price|0 : s.price}</div>` : ''}
      </div>
    </div>
  `).join('');
}

// ── Slot Selection ────────────────────────────────────────────────────────────

function selectService(id) {
  const svc = state.services.find(s => s.id === id);
  if (!svc) return;
  state.selected.service = svc;
  state.weekStart = getMonday(new Date());

  const i = state.services.indexOf(svc);
  document.getElementById('service-bar').innerHTML = `
    <div class="svc-icon">${icon(i)}</div>
    <div>
      <div class="svc-name">${svc.name}</div>
      ${svc.name_sub ? `<div class="svc-name-sub">${svc.name_sub}</div>` : ''}
    </div>
    <div class="svc-meta">
      <span>⏱ ${fmtDuration(svc.duration_mins)}</span>
      ${svc.price ? `<span>💰 $${svc.price}</span>` : ''}
    </div>
  `;

  showScreen('screen-slots');
  loadWeekSlots();
}

function backToServices() { showScreen('screen-services'); }

async function loadWeekSlots() {
  const startStr = fmtDate(state.weekStart);
  const res = await fetch(`/api/book/${SLUG}/week_slots?start=${startStr}&service_id=${state.selected.service.id}`);
  const slots = await res.json();
  renderCalendar(slots);
}

function renderCalendar(slots) {
  const today = new Date(); today.setHours(0,0,0,0);
  const weekEnd = new Date(state.weekStart); weekEnd.setDate(weekEnd.getDate()+6);
  const startM = MONTHS[state.weekStart.getMonth()], endM = MONTHS[weekEnd.getMonth()];
  document.getElementById('month-label').textContent = startM === endM ? startM : `${startM} / ${endM}`;

  const grid = document.getElementById('week-grid');
  grid.innerHTML = '';

  for (let i = 0; i < 7; i++) {
    const d = new Date(state.weekStart); d.setDate(d.getDate() + i);
    const ds = fmtDate(d);
    const daySlots = slots[ds] || [];
    const isToday = d.getTime() === today.getTime();
    const isPast = d < today;

    const col = document.createElement('div');
    col.className = 'day-col';
    col.innerHTML = `
      <div class="day-header">
        <span class="day-letter">${DAY_LETTERS[i]}</span>
        <span class="day-number${isToday ? ' today' : ''}">${d.getDate()}</span>
      </div>
    `;

    if (isPast || daySlots.length === 0) {
      const msg = document.createElement('div');
      msg.className = 'no-slots';
      msg.textContent = isPast ? '' : '—';
      col.appendChild(msg);
    } else {
      daySlots.forEach(time => {
        const btn = document.createElement('button');
        btn.className = 'slot-btn';
        btn.textContent = time;
        btn.onclick = () => openModal(ds, time);
        col.appendChild(btn);
      });
    }
    grid.appendChild(col);
  }
}

function prevWeek() { state.weekStart.setDate(state.weekStart.getDate()-7); loadWeekSlots(); }
function nextWeek() { state.weekStart.setDate(state.weekStart.getDate()+7); loadWeekSlots(); }
function goToToday() { state.weekStart = getMonday(new Date()); loadWeekSlots(); }

// ── Confirm Modal ─────────────────────────────────────────────────────────────

function openModal(dateStr, time) {
  state.selected.date = dateStr;
  state.selected.time = time;
  const svc = state.selected.service;
  const i = state.services.indexOf(svc);

  document.getElementById('modal-icon').textContent = icon(i);
  document.getElementById('modal-svc').textContent = svc.name;
  document.getElementById('modal-dt').textContent = fmtDisplay(dateStr, time);
  document.getElementById('modal-comment').value = '';
  document.getElementById('char-count').textContent = '0 / 100';
  document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal() { document.getElementById('modal-overlay').classList.add('hidden'); }

document.addEventListener('DOMContentLoaded', () => {
  const ta = document.getElementById('modal-comment');
  if (ta) ta.addEventListener('input', () => {
    document.getElementById('char-count').textContent = `${ta.value.length} / 100`;
  });
});

function proceedToLogin() {
  state.selected.comment = document.getElementById('modal-comment').value.trim();
  closeModal();
  const svc = state.selected.service;
  const i = state.services.indexOf(svc);
  document.getElementById('login-icon').textContent = icon(i);
  document.getElementById('login-svc').textContent = svc.name;
  document.getElementById('login-dt').textContent = fmtDisplay(state.selected.date, state.selected.time);
  showScreen('screen-login');
}

// ── Login & Book ──────────────────────────────────────────────────────────────

function backToSlots() { showScreen('screen-slots'); }

async function submitBooking() {
  const name = document.getElementById('cust-name').value.trim();
  const phone = document.getElementById('cust-phone').value.trim();
  if (!name || !phone) { alert('请填写姓名和手机号码。'); return; }

  const btn = document.getElementById('btn-book');
  btn.disabled = true; btn.textContent = '预约中...';

  try {
    const res = await fetch(`/api/book/${SLUG}/create`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        service_id: state.selected.service.id,
        customer_name: name,
        phone,
        appointment_dt: `${state.selected.date} ${state.selected.time}`,
        comment: state.selected.comment,
      }),
    });
    const data = await res.json();

    if (data.success) {
      document.getElementById('success-details').innerHTML = `
        <p>👤 <strong>${esc(name)}</strong></p>
        <p>✂️ <strong>${esc(data.service)}</strong></p>
        <p>📅 <strong>${fmtDisplay(state.selected.date, state.selected.time)}</strong></p>
        ${state.selected.comment ? `<p>💬 ${esc(state.selected.comment)}</p>` : ''}
      `;
      loadWeekSlots();
      showScreen('screen-success');
    } else {
      alert(data.error || '预约失败，请重试。');
      btn.disabled = false; btn.textContent = '确认预约';
    }
  } catch {
    alert('网络错误，请重试。');
    btn.disabled = false; btn.textContent = '确认预约';
  }
}

function resetBooking() {
  state.selected = { service: null, date: null, time: null, comment: '' };
  document.getElementById('cust-name').value = '';
  document.getElementById('cust-phone').value = '';
  document.getElementById('btn-book').disabled = false;
  document.getElementById('btn-book').textContent = '确认预约';
  showScreen('screen-services');
}

loadServices();
