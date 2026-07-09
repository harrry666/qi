const SLUG = document.body.dataset.slug;
const LANG = document.body.dataset.lang === 'en' ? 'en' : 'zh';
const DAY_LETTERS = ['M','T','W','T','F','S','S'];
const MONTHS_ZH = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
const MONTHS_EN = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MONTHS = LANG === 'en' ? MONTHS_EN : MONTHS_ZH;
const WEEKDAYS = LANG === 'en'
  ? ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
  : ['周日','周一','周二','周三','周四','周五','周六'];
const ICONS = ['✂️','💇','💈','💅','🧖','💆','🪮'];

const T = {
  zh: {
    step_staff: '② 选员工', step_time: '② 选时间', step_time_s: '③ 选时间',
    step_info: '③ 填信息', step_info_s: '④ 填信息', step_confirm: '④ 确认', step_confirm_s: '⑤ 确认',
    no_services: '暂无可用服务。', popular: '热门', price_tbd: '价格面议', any_staff: '任意可用',
    phone_invalid: '请先输入有效的10位美国手机号', sending: '发送中...', resend: '重新发送',
    send_code: '获取验证码', send_fail: '发送失败，请重试',
    need_name: '请填写姓名。', need_code: '请先点击「获取验证码」并输入收到的验证码',
    need_consent: '请勾选短信同意框以确认预约。',
    lbl_service: '服务', lbl_staff: '服务人员', lbl_datetime: '日期时间', lbl_price: '价格',
    lbl_name: '姓名', lbl_phone: '手机号', lbl_note: '备注',
    submit_confirm: '✅ 确认提交', submitting: '提交中...', book_fail: '预约失败，请重试。',
    net_err: '网络错误，请重试。', next: '下一步 →',
  },
  en: {
    step_staff: '② Staff', step_time: '② Time', step_time_s: '③ Time',
    step_info: '③ Info', step_info_s: '④ Info', step_confirm: '④ Confirm', step_confirm_s: '⑤ Confirm',
    no_services: 'No services available yet.', popular: 'Popular', price_tbd: 'Price on request', any_staff: 'Any available',
    phone_invalid: 'Please enter a valid 10-digit US phone number', sending: 'Sending...', resend: 'Resend',
    send_code: 'Send code', send_fail: 'Failed to send, please try again',
    need_name: 'Please enter your name.', need_code: 'Please tap "Send code" and enter the code you receive',
    need_consent: 'Please check the SMS consent box to confirm your appointment.',
    lbl_service: 'Service', lbl_staff: 'Staff', lbl_datetime: 'Date & time', lbl_price: 'Price',
    lbl_name: 'Name', lbl_phone: 'Phone', lbl_note: 'Note',
    submit_confirm: '✅ Confirm booking', submitting: 'Submitting...', book_fail: 'Booking failed, please try again.',
    net_err: 'Network error, please try again.', next: 'Next →',
  },
};
function L(k) { return (T[LANG] && T[LANG][k]) || T.zh[k] || k; }

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

let state = {
  services: [],
  staffList: [],
  selected: { service: null, date: null, time: null, comment: '', staff: null },
  weekStart: null,
};

let hasStaffStep = false;

let _codeCountdown = 0;
let _codeTimer = null;

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
  const dt = new Date(y, m-1, d);
  if (LANG === 'en') return `${MONTHS_EN[m-1]} ${d}, ${WEEKDAYS[dt.getDay()]}  ${time}`;
  return `${MONTHS_ZH[m-1]}${d}日 ${WEEKDAYS[dt.getDay()]}  ${time}`;
}

function fmtDuration(mins) {
  if (LANG === 'en') {
    if (mins < 60) return `${mins} min`;
    const h = Math.floor(mins/60), m = mins % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
  }
  if (mins < 60) return `${mins}分钟`;
  const h = Math.floor(mins/60), m = mins % 60;
  return m ? `${h}小时${m}分钟` : `${h}小时`;
}
function fmtSvcDuration(s) {
  if (s.duration_min_mins) {
    return LANG === 'en'
      ? `${s.duration_min_mins}-${s.duration_mins} min`
      : `${s.duration_min_mins}-${s.duration_mins}分钟`;
  }
  return fmtDuration(s.duration_mins);
}

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  const stepsEl = document.querySelector('.booking-steps');
  if (!stepsEl) return;
  if (id === 'screen-success') { stepsEl.style.display = 'none'; return; }
  stepsEl.style.display = 'flex';
  const order = hasStaffStep
    ? ['screen-services', 'screen-staff', 'screen-slots', 'screen-login', 'screen-confirm']
    : ['screen-services', 'screen-slots', 'screen-login', 'screen-confirm'];
  const stepIds = hasStaffStep
    ? ['step-1', 'step-staff', 'step-2', 'step-3', 'step-4']
    : ['step-1', 'step-2', 'step-3', 'step-4'];
  const activeIdx = order.indexOf(id);
  stepIds.forEach((sid, i) => {
    const el = document.getElementById(sid);
    if (!el) return;
    el.classList.remove('active', 'done');
    if (i === activeIdx) el.classList.add('active');
    else if (activeIdx > -1 && i < activeIdx) el.classList.add('done');
  });
}

function setStaffStepVisible(on) {
  hasStaffStep = on;
  const stepStaff = document.getElementById('step-staff');
  const lineStaff = document.getElementById('line-staff');
  if (stepStaff) stepStaff.style.display = on ? '' : 'none';
  if (lineStaff) lineStaff.style.display = on ? '' : 'none';
  if (stepStaff) stepStaff.textContent = L('step_staff');
  document.getElementById('step-2').textContent = on ? L('step_time_s') : L('step_time');
  document.getElementById('step-3').textContent = on ? L('step_info_s') : L('step_info');
  document.getElementById('step-4').textContent = on ? L('step_confirm_s') : L('step_confirm');
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
    list.innerHTML = `<p style="color:var(--muted);padding:20px 0">${L('no_services')}</p>`;
    return;
  }
  list.innerHTML = state.services.map((s, i) => {
    const hotBadge = i === 0 ? `<span style="display:inline-block;background:#FBF4E3;color:#A8882A;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;margin-left:6px;border:1px solid #E8D59A;">${L('popular')}</span>` : '';
    const priceHtml = s.price
      ? `<div class="svc-price-main">$${s.price % 1 === 0 ? s.price|0 : s.price}</div>`
      : `<div class="svc-price-tbd">${L('price_tbd')}</div>`;
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
        ${s.duration_mins ? `<div class="svc-duration">${fmtSvcDuration(s)}</div>` : ''}
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

async function selectService(id) {
  const svc = state.services.find(s => s.id === id);
  if (!svc) return;
  state.selected.service = svc;
  state.selected.staff = null;
  state.weekStart = getMonday(new Date());
  renderServiceBar();

  let staff = [];
  try {
    const res = await fetch(`/api/book/${SLUG}/staff?service_id=${svc.id}`);
    staff = await res.json();
  } catch { staff = []; }

  if (Array.isArray(staff) && staff.length) {
    state.staffList = staff;
    setStaffStepVisible(true);
    renderStaff();
    showScreen('screen-staff');
  } else {
    state.staffList = [];
    setStaffStepVisible(false);
    showScreen('screen-slots');
    loadWeekSlots();
  }
}

function renderServiceBar() {
  const svc = state.selected.service;
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
      <span>⏱ ${fmtSvcDuration(svc)}</span>
      ${svc.price ? `<span>💰 $${svc.price}</span>` : ''}
    </div>
  `;
}

function renderStaff() {
  const wrap = document.getElementById('staff-list');
  const cards = state.staffList.map(s => {
    const av = s.avatar_url && String(s.avatar_url).startsWith('http')
      ? `<img class="staff-ava" src="${esc(s.avatar_url)}" alt="">`
      : `<div class="staff-ava">${s.emoji ? esc(s.emoji) : esc(String(s.name).slice(0,1))}</div>`;
    return `<div class="staff-card" data-id="${s.id}" onclick="selectStaff(${s.id})">${av}<div class="staff-name">${esc(s.name)}</div></div>`;
  }).join('');
  const anyCard = `<div class="staff-card staff-any selected" data-id="" onclick="selectStaff('')"><div class="staff-ava">✨</div><div class="staff-name">${L('any_staff')}</div></div>`;
  wrap.innerHTML = anyCard + cards;
  state.selected.staff = null;
}

function selectStaff(id) {
  state.selected.staff = (id === '' || id == null) ? null : id;
  document.querySelectorAll('#staff-list .staff-card').forEach(c => c.classList.remove('selected'));
  const sel = document.querySelector(`#staff-list .staff-card[data-id="${id}"]`);
  if (sel) sel.classList.add('selected');
}

function selectedStaffName() {
  if (state.selected.staff == null) return null;
  const s = state.staffList.find(x => x.id == state.selected.staff);
  return s ? s.name : null;
}

function proceedFromStaff() {
  showScreen('screen-slots');
  loadWeekSlots();
}

function backFromSlots() {
  if (hasStaffStep) showScreen('screen-staff');
  else showScreen('screen-services');
}

function backToServices() { showScreen('screen-services'); }

async function loadWeekSlots() {
  const startStr = fmtDate(state.weekStart);
  let url = `/api/book/${SLUG}/week_slots?start=${startStr}&service_id=${state.selected.service.id}`;
  if (state.selected.staff != null) url += `&staff_id=${state.selected.staff}`;
  const res = await fetch(url);
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

async function sendVerifyCode() {
  const phone = document.getElementById('cust-phone').value.trim();
  const phoneDigits = phone.replace(/\D/g, '');
  const phoneValid = phoneDigits.length === 10 || (phoneDigits.length === 11 && phoneDigits[0] === '1');
  const phoneError = document.getElementById('phone-error');
  if (!phoneValid) {
    phoneError.textContent = L('phone_invalid');
    phoneError.style.display = 'block';
    return;
  }
  phoneError.style.display = 'none';
  const btn = document.getElementById('btn-send-code');
  btn.disabled = true;
  btn.textContent = L('sending');
  try {
    const res = await fetch('/api/verify/send', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ phone }),
    });
    const data = await res.json();
    if (data.sent) {
      document.getElementById('code-row').style.display = 'block';
      _codeCountdown = 60;
      btn.textContent = `${_codeCountdown}s`;
      _codeTimer = setInterval(() => {
        _codeCountdown--;
        if (_codeCountdown <= 0) {
          clearInterval(_codeTimer); _codeTimer = null;
          btn.disabled = false; btn.textContent = L('resend');
        } else {
          btn.textContent = `${_codeCountdown}s`;
        }
      }, 1000);
    } else {
      btn.disabled = false; btn.textContent = L('send_code');
      alert(data.error || L('send_fail'));
    }
  } catch {
    btn.disabled = false; btn.textContent = L('send_code');
    alert(L('send_fail'));
  }
}

function showConfirmScreen() {
  const name = document.getElementById('cust-name').value.trim();
  const phone = document.getElementById('cust-phone').value.trim();
  const smsConsent = document.getElementById('sms-consent').checked;
  const phoneDigits = phone.replace(/\D/g, '');
  const phoneValid = phoneDigits.length === 10 || (phoneDigits.length === 11 && phoneDigits[0] === '1');
  const phoneError = document.getElementById('phone-error');
  if (!name) { alert(L('need_name')); return; }
  if (!phoneValid) {
    phoneError.textContent = L('phone_invalid');
    phoneError.style.display = 'block';
    return;
  }
  phoneError.style.display = 'none';
  const codeRow = document.getElementById('code-row');
  if (codeRow) {
    const code = (document.getElementById('cust-code').value || '').trim();
    if (!code) {
      phoneError.textContent = L('need_code');
      phoneError.style.display = 'block';
      return;
    }
  }
  if (!smsConsent) { alert(L('need_consent')); return; }

  const svc = state.selected.service;
  const priceText = svc.price ? `$${svc.price % 1 === 0 ? svc.price|0 : svc.price}` : L('price_tbd');
  const staffName = selectedStaffName();
  const rows = [
    { label: L('lbl_service'), value: esc(svc.name) },
  ];
  if (staffName) rows.push({ label: L('lbl_staff'), value: esc(staffName) });
  rows.push(
    { label: L('lbl_datetime'), value: fmtDisplay(state.selected.date, state.selected.time) },
    { label: L('lbl_price'), value: priceText },
    { label: L('lbl_name'), value: esc(name) },
    { label: L('lbl_phone'), value: esc(phone) },
  );
  if (state.selected.comment) rows.push({ label: L('lbl_note'), value: esc(state.selected.comment) });

  document.getElementById('confirm-rows').innerHTML = rows.map(r =>
    `<div class="confirm-row"><span class="confirm-label">${r.label}</span><span class="confirm-value">${r.value}</span></div>`
  ).join('');

  const submitBtn = document.getElementById('btn-confirm-submit');
  submitBtn.disabled = false;
  submitBtn.textContent = L('submit_confirm');
  showScreen('screen-confirm');
}

async function submitBooking() {
  const name = document.getElementById('cust-name').value.trim();
  const phone = document.getElementById('cust-phone').value.trim();
  const btn = document.getElementById('btn-confirm-submit');
  btn.disabled = true; btn.textContent = L('submitting');

  try {
    const res = await fetch(`/api/book/${SLUG}/create`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        service_id: state.selected.service.id,
        staff_id: state.selected.staff != null ? state.selected.staff : null,
        customer_name: name,
        phone,
        appointment_dt: `${state.selected.date} ${state.selected.time}`,
        comment: state.selected.comment,
        hp: document.getElementById('hp_website') ? document.getElementById('hp_website').value : '',
        verify_code: document.getElementById('cust-code') ? document.getElementById('cust-code').value.trim() : '',
      }),
    });
    const data = await res.json();

    if (data.success) {
      document.getElementById('success-details').innerHTML = `
        <p>👤 <strong>${esc(name)}</strong></p>
        <p>✂️ <strong>${esc(data.service)}</strong></p>
        ${selectedStaffName() ? `<p>💈 <strong>${esc(selectedStaffName())}</strong></p>` : ''}
        <p>📅 <strong>${fmtDisplay(state.selected.date, state.selected.time)}</strong></p>
        ${state.selected.comment ? `<p>💬 ${esc(state.selected.comment)}</p>` : ''}
      `;
      loadWeekSlots();
      showScreen('screen-success');
      launchConfetti();
    } else {
      alert(data.error || L('book_fail'));
      btn.disabled = false; btn.textContent = L('submit_confirm');
    }
  } catch {
    alert(L('net_err'));
    btn.disabled = false; btn.textContent = L('submit_confirm');
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
  state.selected = { service: null, date: null, time: null, comment: '', staff: null };
  state.staffList = [];
  setStaffStepVisible(false);
  document.getElementById('cust-name').value = '';
  document.getElementById('cust-phone').value = '';
  document.getElementById('sms-consent').checked = false;
  document.getElementById('btn-book').disabled = false;
  document.getElementById('btn-book').textContent = L('next');
  const phoneError = document.getElementById('phone-error');
  if (phoneError) { phoneError.style.display = 'none'; phoneError.textContent = ''; }
  if (_codeTimer) { clearInterval(_codeTimer); _codeTimer = null; }
  const codeEl = document.getElementById('cust-code');
  if (codeEl) codeEl.value = '';
  const codeRow = document.getElementById('code-row');
  if (codeRow) codeRow.style.display = 'none';
  const sendBtn = document.getElementById('btn-send-code');
  if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = L('send_code'); }
  showScreen('screen-services');
}

loadServices();
