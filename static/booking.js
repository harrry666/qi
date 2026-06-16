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
  const stepsEl = document.querySelector('.booking-steps');
  if (!stepsEl) return;
  const stepMap = { 'screen-services': 1, 'screen-slots': 2, 'screen-login': 3, 'screen-confirm': 4, 'screen-success': null };
  const activeStep = stepMap[id];
  if (activeStep === null) { stepsEl.style.display = 'none'; return; }
  stepsEl.style.display = 'flex';
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`step-${i}`);
    if (!el) continue;
    el.classList.remove('active', 'done');
    if (i === activeStep) el.classList.add('active');
    else if (i < activeStep) el.classList.add('done');
  }
}

// ── Services ─────────────────────────────────────────────────────────────────

async function loadServices() {
  const list = document.getElementById('service-list');
  list.innerHTML = [1,2,3].map(()=>'<div class="skel-item"></div>').join('');
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
  list.innerHTML = state.services.map((s, i) => {
    const hotBadge = i === 0 ? '<span style="display:inline-block;background:#FBF4E3;color:#A8882A;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;margin-left:6px;border:1px solid #E8D59A;">热门</span>' : '';
    const priceHtml = s.price
      ? `<div class="svc-price-main">$${s.price % 1 === 0 ? s.price|0 : s.price}</div>`
      : '<div class="svc-price-tbd">价格面议</div>';
    const iconSrc = s.icon_url && s.icon_url.startsWith('http') ? s.icon_url : null;
    const iconHtml = iconSrc
      ? `<img src="${iconSrc}" style="width:40px;height:40px;object-fit:cover;border-radius:6px">`
      : (s.emoji ? esc(s.emoji) : icon(i));
    return `
    <div class="service-item" onclick="selectService(${s.id})">
      <div class="svc-icon">${iconHtml}</div>
      <div class="svc-info">
        <div class="svc-name">${esc(s.name)}${hotBadge}</div>
        ${s.name_sub ? `<div class="svc-name-sub">${esc(s.name_sub)}</div>` : ''}
        ${priceHtml}
      </div>
      <div class="svc-meta">
        ${s.duration_mins ? `<div class="svc-duration">${fmtDuration(s.duration_mins)}</div>` : ''}
      </div>
    </div>
  `;
  }).join('');
  animateServices();
}

function animateServices() {
  const items = document.querySelectorAll('.service-item');
  items.forEach(function(el, i) {
    setTimeout(function() {
      el.classList.add('svc-visible');
    }, i * 60);
  });
}

// ── Slot Selection ────────────────────────────────────────────────────────────

function selectService(id) {
  const svc = state.services.find(s => s.id === id);
  if (!svc) return;
  state.selected.service = svc;
  state.weekStart = getMonday(new Date());

  const i = state.services.indexOf(svc);
  const barIconSrc = svc.icon_url && svc.icon_url.startsWith('http') ? svc.icon_url : null;
  const barIconHtml = barIconSrc
    ? `<img src="${barIconSrc}" style="width:40px;height:40px;object-fit:cover;border-radius:6px">`
    : icon(i);
  document.getElementById('service-bar').innerHTML = `
    <div class="svc-icon">${barIconHtml}</div>
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

  const modalIconEl = document.getElementById('modal-icon');
  if (svc.icon_url && svc.icon_url.startsWith('http')) {
    modalIconEl.innerHTML = `<img src="${svc.icon_url}" style="width:40px;height:40px;object-fit:cover;border-radius:6px">`;
  } else {
    modalIconEl.textContent = svc.emoji || icon(i);
  }
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
  const phoneInput = document.getElementById('cust-phone');
  if (phoneInput) phoneInput.addEventListener('input', () => {
    document.getElementById('phone-error').style.display = 'none';
  });
});

function proceedToLogin() {
  state.selected.comment = document.getElementById('modal-comment').value.trim();
  closeModal();
  const svc = state.selected.service;
  const i = state.services.indexOf(svc);
  const loginIconEl = document.getElementById('login-icon');
  if (svc.icon_url && svc.icon_url.startsWith('http')) {
    loginIconEl.innerHTML = `<img src="${svc.icon_url}" style="width:40px;height:40px;object-fit:cover;border-radius:6px">`;
  } else {
    loginIconEl.textContent = svc.emoji || icon(i);
  }
  document.getElementById('login-svc').textContent = svc.name;
  document.getElementById('login-dt').textContent = fmtDisplay(state.selected.date, state.selected.time);
  showScreen('screen-login');
}

// ── Login & Book ──────────────────────────────────────────────────────────────

function backToSlots() { showScreen('screen-slots'); }

function showConfirmScreen() {
  const name = document.getElementById('cust-name').value.trim();
  const phone = document.getElementById('cust-phone').value.trim();
  const smsConsent = document.getElementById('sms-consent').checked;
  const phoneDigits = phone.replace(/\D/g, '');
  const phoneValid = phoneDigits.length === 10 || (phoneDigits.length === 11 && phoneDigits[0] === '1');
  const phoneError = document.getElementById('phone-error');
  if (!name) { alert('请填写姓名。'); return; }
  if (!phoneValid) {
    phoneError.textContent = '请输入有效的10位美国手机号';
    phoneError.style.display = 'block';
    return;
  }
  phoneError.style.display = 'none';
  if (!smsConsent) { alert('Please check the SMS consent box to confirm your appointment.'); return; }

  const svc = state.selected.service;
  const priceText = svc.price ? `$${svc.price % 1 === 0 ? svc.price|0 : svc.price}` : '价格面议';
  const rows = [
    { label: '服务', value: esc(svc.name) },
    { label: '日期时间', value: fmtDisplay(state.selected.date, state.selected.time) },
    { label: '价格', value: priceText },
    { label: '姓名', value: esc(name) },
    { label: '手机号', value: esc(phone) },
  ];
  if (state.selected.comment) rows.push({ label: '备注', value: esc(state.selected.comment) });

  document.getElementById('confirm-rows').innerHTML = rows.map(r =>
    `<div class="confirm-row"><span class="confirm-label">${r.label}</span><span class="confirm-value">${r.value}</span></div>`
  ).join('');

  const submitBtn = document.getElementById('btn-confirm-submit');
  submitBtn.disabled = false;
  submitBtn.textContent = '✅ 确认提交';
  showScreen('screen-confirm');
}

async function submitBooking() {
  const name = document.getElementById('cust-name').value.trim();
  const phone = document.getElementById('cust-phone').value.trim();
  const btn = document.getElementById('btn-confirm-submit');
  btn.disabled = true; btn.textContent = '提交中...';

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
      launchConfetti();
    } else {
      alert(data.error || '预约失败，请重试。');
      btn.disabled = false; btn.textContent = '✅ 确认提交';
    }
  } catch {
    alert('网络错误，请重试。');
    btn.disabled = false; btn.textContent = '✅ 确认提交';
  }
}

function launchConfetti() {
  const colors = ['#C9A84C','#E8C96A','#FBF4E3','#A8882A','#ffffff'];
  for (let i = 0; i < 60; i++) {
    const el = document.createElement('div');
    el.style.cssText = `position:fixed;pointer-events:none;z-index:9999;width:${4+Math.random()*6}px;height:${4+Math.random()*6}px;background:${colors[Math.floor(Math.random()*colors.length)]};border-radius:${Math.random()>0.5?'50%':'2px'};left:${20+Math.random()*60}%;top:-10px;opacity:1;`;
    document.body.appendChild(el);
    const duration = 1200 + Math.random() * 1000;
    const xDrift = (Math.random() - 0.5) * 200;
    el.animate([
      { transform: `translate(0,0) rotate(0deg)`, opacity: 1 },
      { transform: `translate(${xDrift}px, ${window.innerHeight + 50}px) rotate(${360 + Math.random()*360}deg)`, opacity: 0 }
    ], { duration, easing: 'cubic-bezier(0.25,0.46,0.45,0.94)', fill: 'forwards' }).onfinish = () => el.remove();
  }
}

function resetBooking() {
  state.selected = { service: null, date: null, time: null, comment: '' };
  document.getElementById('cust-name').value = '';
  document.getElementById('cust-phone').value = '';
  document.getElementById('sms-consent').checked = false;
  document.getElementById('btn-book').disabled = false;
  document.getElementById('btn-book').textContent = '下一步 →';
  const phoneError = document.getElementById('phone-error');
  if (phoneError) { phoneError.style.display = 'none'; phoneError.textContent = ''; }
  showScreen('screen-services');
}

loadServices();
