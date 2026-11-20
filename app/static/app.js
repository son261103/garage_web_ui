/* Garage Web UI — frontend (tiếng Việt).
   Chỉ gọi API của backend này (/api/...). Không bao giờ cầm admin token. */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const TABS = [
  { id: 'dashboard', label: 'Tổng quan', icon: 'layout-dashboard', title: 'Tổng quan', sub: 'Tình trạng cluster, dung lượng và thống kê' },
  { id: 'buckets', label: 'Bucket', icon: 'bucket', title: 'Bucket', sub: 'Danh sách bucket, tạo / xóa bucket' },
  { id: 'keys', label: 'Khóa', icon: 'key-round', title: 'Khóa truy cập', sub: 'Tạo key S3, gán quyền cho từng bucket' },
  { id: 'objects', label: 'File', icon: 'folder-open', title: 'File trong bucket', sub: 'Duyệt, tải lên, tải xuống, xem ảnh' },
  { id: 'layout', label: 'Bố cục', icon: 'network', title: 'Bố cục cluster', sub: 'Layout, node và dung lượng đã gán' },
];

let currentTab = 'dashboard';
let buckets = [];
let keys = [];
let objBucket = '';
let objPrefix = '';
let permCtx = null;
let confirmAction = null;
let s3Configured = false;

/* ---------- chủ đề sáng / tối ---------- */
function initTheme() {
  const saved = localStorage.getItem('garage-ui-theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const dark = saved ? saved === 'dark' : prefersDark;
  document.documentElement.classList.toggle('dark', dark);
}
function toggleTheme() {
  const dark = !document.documentElement.classList.contains('dark');
  document.documentElement.classList.toggle('dark', dark);
  localStorage.setItem('garage-ui-theme', dark ? 'dark' : 'light');
  refreshIcons();
}

/* ---------- tiện ích ---------- */
function refreshIcons() { if (window.lucide) window.lucide.createIcons(); }
function fmtBytes(n) {
  n = Number(n) || 0;
  if (n === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return (n / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}
function fmtDate(s) {
  if (!s) return '—';
  const d = new Date(s);
  return isNaN(d) ? String(s) : d.toLocaleString('vi-VN');
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function toast(msg, ok = true) {
  const el = document.createElement('div');
  el.className = 'toast ' + (ok ? 'ok' : 'err');
  el.innerHTML = `<i data-lucide="${ok ? 'check-circle-2' : 'alert-circle'}" class="w-5 h-5 shrink-0"></i><span>${esc(msg)}</span>`;
  $('#toasts').appendChild(el);
  refreshIcons();
  setTimeout(() => el.remove(), 5000);
}
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data.detail || data.message || `Lỗi HTTP ${res.status}`);
  return data;
}
function copyText(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.select();
  navigator.clipboard?.writeText(el.value).then(
    () => toast('Đã sao chép.'),
    () => { document.execCommand('copy'); toast('Đã sao chép.'); }
  );
}
function copyString(text, label = 'Đã sao chép.') {
  navigator.clipboard?.writeText(text).then(() => toast(label), () => toast('Không sao chép được, hãy sao chép thủ công.', false));
}
function openDialog(id) { document.getElementById(id).classList.remove('hidden'); refreshIcons(); }
function closeDialog(id) { document.getElementById(id).classList.add('hidden'); }
function askConfirm(title, text, onYes) {
  $('#confirmTitle').textContent = title;
  $('#confirmText').textContent = text;
  confirmAction = onYes;
  openDialog('confirmDialog');
}
document.getElementById('confirmBtn').addEventListener('click', async () => {
  closeDialog('confirmDialog');
  if (confirmAction) { const fn = confirmAction; confirmAction = null; await fn(); }
});
$$('.dialog').forEach((d) => d.addEventListener('click', (e) => { if (e.target === d) d.classList.add('hidden'); }));

/* ---------- điều hướng ---------- */
function buildNav() {
  $('#nav').innerHTML = TABS.map((t) => `
    <button class="nav-item ${t.id === currentTab ? 'active' : ''}" onclick="switchTab('${t.id}')">
      <i data-lucide="${t.icon}" class="w-4 h-4"></i>${t.label}
    </button>`).join('');
  $('#mobileNav').innerHTML = TABS.map((t) => `
    <button class="btn-secondary !py-1.5 !px-3 ${t.id === currentTab ? '!bg-primary-100 dark:!bg-primary-500/20 !text-primary-700 dark:!text-primary-300 !border-primary-200' : ''}" onclick="switchTab('${t.id}')">${t.label}</button>`).join('');
  refreshIcons();
}
function switchTab(id) {
  currentTab = id;
  const tab = TABS.find((t) => t.id === id);
  $('#pageTitle').textContent = tab.title;
  $('#pageSubtitle').textContent = tab.sub;
  $$('.view').forEach((v) => v.classList.add('hidden'));
  document.getElementById('view-' + id).classList.remove('hidden');
  $('#mobileNav').classList.add('hidden');
  buildNav();
  if (id === 'dashboard') loadDashboard();
  if (id === 'buckets') loadBuckets();
  if (id === 'keys') loadKeys();
  if (id === 'objects') loadObjectsTab();
  if (id === 'layout') loadLayout();
}

/* ---------- bố cục ---------- */
async function loadLayout() {
  const box = $('#layoutBox'), nodesEl = $('#layoutNodes');
  box.innerHTML = '<p class="text-slate-500">Đang tải layout…</p>';
  nodesEl.innerHTML = '';
  try {
    const [layout, status] = await Promise.all([api('/api/layout'), api('/api/status')]);
    const l = layout.layout || {};
    const cap = (status.nodes || []).reduce((s, n) => s + (n.capacity || 0), 0);
    const used = status.totals ? status.totals.bytes : 0;
    box.innerHTML = `
      <div class="flex justify-between"><span>Phiên bản layout</span><b>v${l.version ?? 0}</b></div>
      <div class="flex justify-between mt-1"><span>Trạng thái layout</span><b>${esc(l.status || '—')}</b></div>
      <div class="flex justify-between mt-1"><span>Số node lưu trữ</span><b>${l.storageNodes ?? (status.nodes || []).length}</b></div>
      <div class="flex justify-between mt-1"><span>Dung lượng đã gán</span><b>${fmtBytes(cap)} (40 GB)</b></div>
      <div class="flex justify-between mt-1"><span>Đã dùng / còn trống</span><b>${fmtBytes(used)} / ${fmtBytes(Math.max(0, cap - used))}</b></div>
      <p class="text-xs text-slate-500 dark:text-slate-400 mt-3">Layout chỉ được gán bằng CLI (<code>garage layout assign</code> rồi <code>garage layout apply</code>). Giao diện này chỉ xem, không đổi layout để tránh làm hỏng cluster.</p>`;
    nodesEl.innerHTML = (status.nodes || []).map((n) => `
      <div class="rounded-xl border border-slate-200 dark:border-slate-700 p-4 mb-3 bg-white dark:bg-slate-800">
        <div class="flex items-center gap-2 font-semibold"><i data-lucide="server" class="w-4 h-4 text-primary-500"></i>${esc(n.hostname || n.id)}</div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm mt-2">
          <div><div class="text-xs text-slate-500">Node ID</div><code class="text-xs break-all">${esc(n.id)}</code></div>
          <div><div class="text-xs text-slate-500">Zone</div><b>${esc(n.zone || '—')}</b></div>
          <div><div class="text-xs text-slate-500">Dung lượng gán</div><b>${fmtBytes(n.capacity || 0)}</b></div>
          <div><div class="text-xs text-slate-500">Trống (DataAvail)</div><b>${fmtBytes(n.dataAvail || 0)}</b></div>
          <div><div class="text-xs text-slate-500">Phiên bản</div><b>${esc(n.version || '—')}</b></div>
          <div><div class="text-xs text-slate-500">Trạng thái</div><span class="badge ${n.isUp ? 'badge-ok' : 'badge-err'}">${n.isUp ? 'Đang chạy' : 'Mất kết nối'}</span></div>
        </div>
      </div>`).join('');
    refreshIcons();
  } catch (e) {
    box.innerHTML = `<p class="text-red-500">${esc(e.message)}</p>`;
  }
}

/* ---------- tổng quan ---------- */
async function loadDashboard() {
  try {
    const data = await api('/api/status');
    const health = data.healthStatus || 'healthy';
    const badge = $('#healthBadge');
    badge.className = 'badge ' + (health === 'healthy' ? 'badge-ok' : health === 'degraded' ? 'badge-warn' : 'badge-bad');
    badge.innerHTML = `<span class="dot"></span> ${health === 'healthy' ? 'Khỏe mạnh' : health === 'degraded' ? 'Suy giảm' : 'Không khả dụng'}`;
    const t = data.totals || {};
    const cap = t.capacity || 0;
    const used = t.bytes || 0;
    const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
    $('#statCards').innerHTML = [
      { icon: 'bucket', label: 'Bucket', value: t.buckets ?? 0, cls: 'bg-primary-100 text-primary-600 dark:bg-primary-500/15 dark:text-primary-300' },
      { icon: 'key-round', label: 'Khóa', value: t.keys ?? 0, cls: 'bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300' },
      { icon: 'file', label: 'File (object)', value: (t.objects ?? 0).toLocaleString('vi-VN'), cls: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300' },
      { icon: 'hard-drive', label: 'Đã dùng', value: fmtBytes(used), cls: 'bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300' },
    ].map((s) => `
      <div class="card flex items-center gap-3">
        <div class="stat-icon ${s.cls}"><i data-lucide="${s.icon}" class="w-5 h-5"></i></div>
        <div class="min-w-0"><p class="text-2xl font-extrabold truncate">${s.value}</p><p class="text-xs text-slate-500">${s.label}</p></div>
      </div>`).join('');
    $('#storageBar').style.width = pct.toFixed(1) + '%';
    $('#storageLabel').textContent = `${fmtBytes(used)} / ${cap ? fmtBytes(cap) : '—'} (${pct.toFixed(1)}%)`;
    $('#storageNote').textContent = cap
      ? `Dung lượng đã gán cho cluster là ${fmtBytes(cap)} (layout zone dc1, 40 GB). Còn trống khoảng ${fmtBytes(Math.max(0, cap - used))}.`
      : 'Chưa gán layout cho cluster. Hãy gán layout bằng CLI rồi làm mới trang này.';
    $('#nodeCards').innerHTML = (data.nodes || []).map((n) => `
      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
        <p class="font-mono text-xs truncate" title="${esc(n.id)}">${esc(n.id || '—')}</p>
        <p class="text-xs text-slate-500 mt-1">Zone: <b>${esc(n.zone || '—')}</b> &middot; ${esc(n.hostname || '')}</p>
        <p class="text-xs text-slate-500">Dung lượng: ${fmtBytes(n.capacity)} &middot; Phiên bản: ${esc(n.version || data.garageVersion || '—')}</p>
        <span class="badge ${n.isUp ? 'badge-ok' : 'badge-bad'} mt-2">${n.isUp ? 'Đang chạy' : 'Mất kết nối'}</span>
      </div>`).join('') || '<p class="text-slate-500 text-sm">Chưa thấy node nào.</p>';
    const h = data.health || {};
    $('#healthBox').innerHTML = `
      <div class="flex justify-between"><span>Node đã biết</span><b>${h.knownNodes ?? (data.nodes || []).length}</b></div>
      <div class="flex justify-between"><span>Node đang kết nối</span><b>${h.connectedNodes ?? '—'}</b></div>
      <div class="flex justify-between"><span>Node lưu trữ / đang chạy</span><b>${h.storageNodes ?? '—'} / ${h.storageNodesUp ?? '—'}</b></div>
      <div class="flex justify-between"><span>Partition đủ quorum</span><b>${h.partitionsQuorum ?? '—'} / ${h.partitions ?? 256}</b></div>
      <div class="flex justify-between"><span>Partition hoàn toàn OK</span><b>${h.partitionsAllOk ?? '—'}</b></div>
      <div class="flex justify-between"><span>Phiên bản layout</span><b>v${data.layoutVersion ?? 0}</b></div>
      <div class="flex justify-between"><span>Garage</span><b>${esc(data.garageVersion || '—')}</b></div>`;
    refreshIcons();
  } catch (e) {
    const badge = $('#healthBadge');
    badge.className = 'badge badge-bad';
    badge.innerHTML = '<span class="dot"></span> Không kết nối được Garage';
    $('#globalAlert').innerHTML = `<div class="card !border-red-200 dark:!border-red-500/30 mb-4"><p class="font-semibold text-red-600">Không kết nối được tới Garage: ${esc(e.message)}</p><p class="text-sm text-slate-500 mt-1">Kiểm tra container garage có đang chạy không (<code>docker compose ps</code> trong /opt/garage), và biến <code>GARAGE_ADMIN_URL</code> / <code>GARAGE_ADMIN_TOKEN</code> của container admin-ui.</p></div>`;
    refreshIcons();
  }
}

/* ---------- bucket ---------- */
function bucketCard(b) {
  const pct = b.quotas?.maxSize ? Math.min(100, (b.bytes / b.quotas.maxSize) * 100) : null;
  return `
  <div class="card">
    <div class="flex items-start justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <div class="stat-icon bg-primary-100 text-primary-600 dark:bg-primary-500/15 dark:text-primary-300"><i data-lucide="bucket" class="w-5 h-5"></i></div>
        <div class="min-w-0"><p class="font-bold truncate">${esc(b.name)}</p><p class="text-xs text-slate-400 font-mono truncate">${esc(b.id)}</p></div>
      </div>
      <span class="badge badge-blue">${b.objects.toLocaleString('vi-VN')} file</span>
    </div>
    <div class="flex justify-between text-sm mt-3"><span class="text-slate-500">Dung lượng</span><b>${fmtBytes(b.bytes)}</b></div>
    ${pct !== null ? `<div class="h-2 rounded-full bg-slate-200 dark:bg-slate-700 mt-1 overflow-hidden"><div class="h-full bg-primary-500" style="width:${pct}%"></div></div><p class="text-xs text-slate-400 mt-1">Hạn mức: ${fmtBytes(b.quotas.maxSize)}</p>` : ''}
    <p class="text-xs text-slate-400 mt-2">Tạo ngày: ${fmtDate(b.created)} &middot; Key có quyền: ${b.keys.length}</p>
    <div class="flex flex-wrap gap-2 mt-4">
      <button class="btn-secondary !py-1.5 !px-3" onclick="openObjects('${esc(b.name)}')"><i data-lucide="folder-open" class="w-4 h-4"></i> Mở file</button>
      <button class="btn-secondary !py-1.5 !px-3" onclick="openBucketDetail('${esc(b.id)}')"><i data-lucide="info" class="w-4 h-4"></i> Chi tiết</button>
      <button class="btn-danger !py-1.5 !px-3 ml-auto" onclick="deleteBucket('${esc(b.id)}','${esc(b.name)}')"><i data-lucide="trash-2" class="w-4 h-4"></i> Xóa</button>
    </div>
  </div>`;
}
async function loadBuckets() {
  try {
    const data = await api('/api/buckets');
    buckets = data.buckets || [];
    const q = ($('#bucketSearch').value || '').toLowerCase();
    const list = buckets.filter((b) => !q || b.name.toLowerCase().includes(q));
    $('#bucketList').innerHTML = list.map(bucketCard).join('');
    $('#bucketEmpty').classList.toggle('hidden', list.length > 0);
    refreshIcons();
  } catch (e) { toast(e.message, false); }
}
function openBucketDialog() { $('#newBucketName').value = ''; $('#bucketNameError').classList.add('hidden'); openDialog('bucketDialog'); }
async function createBucket() {
  const name = $('#newBucketName').value.trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$/.test(name)) {
    const err = $('#bucketNameError');
    err.textContent = 'Tên không hợp lệ: 3–63 ký tự, chỉ chữ thường, số, dấu - và dấu ., bắt đầu/kết thúc bằng chữ hoặc số.';
    err.classList.remove('hidden');
    return;
  }
  try {
    await api('/api/buckets', { method: 'POST', body: JSON.stringify({ name }) });
    closeDialog('bucketDialog');
    toast(`Đã tạo bucket “${name}”.`);
    loadBuckets();
  } catch (e) { toast(e.message, false); }
}
function deleteBucket(id, name) {
  askConfirm('Xóa bucket?', `Bucket “${name}” sẽ bị xóa vĩnh viễn. Chỉ xóa được khi bucket đã rỗng — hãy xóa hết file trong bucket trước.`, async () => {
    try { await api(`/api/buckets/${encodeURIComponent(id)}`, { method: 'DELETE' }); toast(`Đã xóa bucket “${name}”.`); loadBuckets(); }
    catch (e) { toast(e.message, false); }
  });
}
async function openBucketDetail(id) {
  try {
    const b = await api(`/api/buckets/${encodeURIComponent(id)}`);
    const keys = (b.keys || []).map((k) => `${esc(k.accessKeyId)} (${esc(k.name)}) — ${k.permissions.read ? 'Đọc ' : ''}${k.permissions.write ? 'Ghi ' : ''}${k.permissions.owner ? 'Chủ ' : ''}`.trim()).join('\n') || 'Chưa có key nào được gán quyền.';
    alert(`Bucket: ${b.name}\nID: ${b.id}\nFile: ${b.objects} — Dung lượng: ${fmtBytes(b.bytes)}\nTạo ngày: ${fmtDate(b.created)}\nHạn mức: ${b.quotas?.maxSize ? fmtBytes(b.quotas.maxSize) : 'không giới hạn'}${b.quotas?.maxObjects ? ' / tối đa ' + b.quotas.maxObjects + ' file' : ''}\n\nKey có quyền:\n${keys}`);
  } catch (e) { toast(e.message, false); }
}

/* ---------- key ---------- */
async function loadKeys() {
  try {
    const data = await api('/api/keys');
    keys = data.keys || [];
    $('#keyList').innerHTML = keys.map((k) => `
      <div class="card">
        <div class="flex flex-wrap items-center gap-3">
          <div class="stat-icon bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300"><i data-lucide="key-round" class="w-5 h-5"></i></div>
          <div class="flex-1 min-w-[180px]">
            <p class="font-bold">${esc(k.name || '(không tên)')}</p>
            <p class="text-xs font-mono text-slate-400 break-all">${esc(k.id)}</p>
            <p class="text-xs text-slate-500 mt-0.5">Tạo ngày: ${fmtDate(k.created)}${k.canCreateBucket ? ' &middot; Được tạo bucket' : ''}</p>
          </div>
          <span class="badge badge-pink">${(k.buckets || []).length} bucket được gán quyền</span>
          <button class="btn-secondary !py-1.5 !px-3" onclick="copyString('${esc(k.id)}','Đã sao chép Key ID.')">Sao chép Key ID</button>
          <button class="btn-danger !py-1.5 !px-3" onclick="deleteKey('${esc(k.id)}','${esc(k.name || k.id)}')"><i data-lucide="trash-2" class="w-4 h-4"></i> Xóa</button>
        </div>
        <div class="mt-3 space-y-2">
          ${(buckets.length ? buckets.map((b) => {
            const perm = (k.buckets || []).find((x) => x.bucketId === b.id);
            const p = perm?.permissions || { read: false, write: false, owner: false };
            return `<div class="flex flex-wrap items-center gap-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
              <span class="font-medium flex-1 min-w-[120px]">${esc(b.name)}</span>
              <span class="badge ${p.read ? 'badge-ok' : 'badge-muted'}">Đọc</span>
              <span class="badge ${p.write ? 'badge-ok' : 'badge-muted'}">Ghi</span>
              <span class="badge ${p.owner ? 'badge-ok' : 'badge-muted'}">Chủ</span>
              <button class="btn-secondary !py-1 !px-2.5 !text-xs" onclick="openPermDialog('${esc(b.id)}','${esc(b.name)}','${esc(k.id)}',${p.read},${p.write},${p.owner})">Đổi quyền</button>
            </div>`;
          }).join('') : '<p class="text-sm text-slate-500">Chưa có bucket nào để gán quyền. Hãy tạo bucket trước.</p>')}
        </div>
      </div>`).join('');
    $('#keyEmpty').classList.toggle('hidden', keys.length > 0);
    refreshIcons();
  } catch (e) { toast(e.message, false); }
}
function openKeyDialog() { $('#newKeyName').value = ''; openDialog('keyDialog'); }
async function createKey() {
  const name = $('#newKeyName').value.trim();
  if (!name) { toast('Hãy nhập tên key, vd: app-key.', false); return; }
  try {
    const created = await api('/api/keys', { method: 'POST', body: JSON.stringify({ name }) });
    closeDialog('keyDialog');
    $('#secretKeyId').value = created.id || '';
    $('#secretKeyValue').value = created.secretAccessKey || '';
    $('#secretKeyValue').type = 'password';
    openDialog('secretDialog');
    toast(`Đã tạo key “${name}”.`);
    loadKeys();
  } catch (e) { toast(e.message, false); }
}
function toggleSecret() {
  const el = $('#secretKeyValue');
  el.type = el.type === 'password' ? 'text' : 'password';
}
function deleteKey(id, name) {
  askConfirm('Xóa key?', `Key “${name}” sẽ bị xóa và không dùng được nữa. Các ứng dụng đang dùng key này sẽ không truy cập được S3.`, async () => {
    try { await api(`/api/keys/${encodeURIComponent(id)}`, { method: 'DELETE' }); toast(`Đã xóa key “${name}”.`); loadKeys(); }
    catch (e) { toast(e.message, false); }
  });
}
function openPermDialog(bucketId, bucketName, keyId, read, write, owner) {
  permCtx = { bucketId, keyId };
  $('#permSub').textContent = `Key ${keyId} trên bucket “${bucketName}”.`;
  $('#permRead').checked = !!read;
  $('#permWrite').checked = !!write;
  $('#permOwner').checked = !!owner;
  openDialog('permDialog');
}
async function savePermissions() {
  if (!permCtx) return;
  try {
    await api(`/api/buckets/${encodeURIComponent(permCtx.bucketId)}/permissions`, {
      method: 'POST',
      body: JSON.stringify({ accessKeyId: permCtx.keyId, read: $('#permRead').checked, write: $('#permWrite').checked, owner: $('#permOwner').checked }),
    });
    closeDialog('permDialog');
    toast('Đã lưu quyền.');
    loadKeys(); loadBuckets();
  } catch (e) { toast(e.message, false); }
}

/* ---------- file ---------- */
async function loadObjectsTab() {
  try {
    const cfg = await api('/api/config');
    s3Configured = !!cfg.s3Configured;
    $('#s3Warning').innerHTML = s3Configured ? '' : `
      <div class="card !border-amber-200 dark:!border-amber-500/30 mb-4">
        <p class="font-semibold text-amber-700 dark:text-amber-300 flex items-center gap-2"><i data-lucide="alert-triangle" class="w-5 h-5"></i> Chưa cấu hình key S3 để duyệt file</p>
        <p class="text-sm text-slate-600 dark:text-slate-300 mt-1">Tab File cần 1 key S3 có quyền đọc/ghi bucket. Cách làm: tạo key ở tab <b>Khóa</b> (vd <code>app-key</code>), gán quyền đọc/ghi cho bucket, rồi thêm 2 biến <code>S3_ACCESS_KEY_ID</code> và <code>S3_SECRET_ACCESS_KEY</code> vào môi trường của container admin-ui và chạy lại <code>docker compose up -d admin-ui</code>. Các tab Tổng quan, Bucket, Khóa, Bố cục vẫn dùng được bình thường.</p>
      </div>`;
    refreshIcons();
  } catch (e) { /* không chặn tab File */ }
  try {
    const data = await api('/api/buckets');
    buckets = data.buckets || [];
    const sel = $('#objBucket');
    const prev = sel.value || objBucket;
    sel.innerHTML = buckets.map((b) => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
    if (buckets.length) {
      objBucket = buckets.some((b) => b.name === prev) ? prev : buckets[0].name;
      sel.value = objBucket;
      loadObjects();
    } else {
      $('#objectList').innerHTML = '';
      $('#objectEmpty').classList.remove('hidden');
    }
  } catch (e) { toast(e.message, false); }
}
function onBucketChange() { objBucket = $('#objBucket').value; objPrefix = ''; loadObjects(); }
function openObjects(bucketName) { switchTab('objects'); objBucket = bucketName; objPrefix = ''; setTimeout(loadObjectsTab, 0); setTimeout(() => { $('#objBucket').value = bucketName; loadObjects(); }, 50); }
function breadcrumbHtml() {
  const parts = objPrefix.split('/').filter(Boolean);
  let html = `<button class="hover:text-primary-600 font-semibold" onclick="goPrefix('')">${esc(objBucket || 'Bucket')}</button>`;
  let acc = '';
  parts.forEach((p) => {
    acc += p + '/';
    html += ` <span class="text-slate-400">/</span> <button class="hover:text-primary-600" onclick="goPrefix('${esc(acc)}')">${esc(p)}</button>`;
  });
  return html;
}
function goPrefix(prefix) { objPrefix = prefix; loadObjects(); }
async function loadObjects() {
  if (!objBucket) return;
  $('#breadcrumb').innerHTML = breadcrumbHtml();
  const search = $('#objSearch').value.trim();
  try {
    const data = await api(`/api/objects?bucket=${encodeURIComponent(objBucket)}&prefix=${encodeURIComponent(objPrefix)}&search=${encodeURIComponent(search)}`);
    const rows = [];
    (data.folders || []).forEach((f) => rows.push(`
      <div class="obj-row" onclick="goPrefix('${esc(f.key)}')">
        <i data-lucide="folder" class="w-5 h-5 text-amber-500 shrink-0"></i>
        <span class="flex-1 font-medium truncate">${esc(f.name)}</span>
        <span class="text-xs text-slate-400">Thư mục</span>
        <button class="btn-icon !w-8 !h-8" title="Xóa thư mục" onclick="event.stopPropagation(); deleteObject('${esc(f.key)}', true)"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
      </div>`));
    (data.files || []).forEach((f) => rows.push(`
      <div class="obj-row" onclick="${f.isImage ? `openPreview('${esc(f.key)}', ${f.size})` : `downloadObject('${esc(f.key)}')`}">
        <i data-lucide="${f.isImage ? 'image' : 'file'}" class="w-5 h-5 ${f.isImage ? 'text-primary-500' : 'text-slate-400'} shrink-0"></i>
        <span class="flex-1 min-w-0"><span class="font-medium truncate block">${esc(f.name)}</span><span class="text-xs text-slate-400">${esc(f.key)}</span></span>
        <span class="text-xs text-slate-500 whitespace-nowrap">${fmtBytes(f.size)}</span>
        <span class="text-xs text-slate-400 whitespace-nowrap hidden sm:inline">${fmtDate(f.lastModified)}</span>
        <button class="btn-icon !w-8 !h-8" title="Sao chép key" onclick="event.stopPropagation(); copyString('${esc(f.key)}','Đã sao chép key của file.')"><i data-lucide="copy" class="w-4 h-4"></i></button>
        <button class="btn-icon !w-8 !h-8" title="Tải xuống" onclick="event.stopPropagation(); downloadObject('${esc(f.key)}')"><i data-lucide="download" class="w-4 h-4"></i></button>
        <button class="btn-icon !w-8 !h-8" title="Xóa file" onclick="event.stopPropagation(); deleteObject('${esc(f.key)}', false)"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
      </div>`));
    $('#objectList').innerHTML = rows.join('');
    $('#objectEmpty').classList.toggle('hidden', rows.length > 0);
    refreshIcons();
  } catch (e) {
    $('#objectList').innerHTML = '';
    $('#objectEmpty').classList.add('hidden');
    toast(e.message, false);
  }
}
async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length || !objBucket) return;
  const prog = $('#uploadProgress');
  prog.classList.remove('hidden');
  prog.textContent = `Đang tải lên ${files.length} file…`;
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  try {
    const res = await fetch(`/api/objects/upload?bucket=${encodeURIComponent(objBucket)}&prefix=${encodeURIComponent(objPrefix)}`, { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Lỗi HTTP ${res.status}`);
    const ok = (data.uploaded || []).length;
    const fail = (data.failed || []).length;
    prog.textContent = `Đã tải lên ${ok} file${fail ? `, thất bại ${fail} file` : ''}.`;
    toast(fail ? `Đã tải lên ${ok} file, thất bại ${fail} file.` : `Đã tải lên ${ok} file.`, !fail);
    $('#uploadInput').value = '';
    loadObjects(); loadBuckets();
  } catch (e) {
    prog.textContent = '';
    toast(e.message, false);
  }
}
function downloadObject(key) {
  window.open(`/api/objects/download?bucket=${encodeURIComponent(objBucket)}&key=${encodeURIComponent(key)}`, '_blank');
}
function openPreview(key, size) {
  $('#previewTitle').textContent = key.split('/').pop();
  $('#previewMeta').textContent = `${key} • ${fmtBytes(size)}`;
  $('#previewImg').src = `/api/objects/preview?bucket=${encodeURIComponent(objBucket)}&key=${encodeURIComponent(key)}`;
  openDialog('previewDialog');
}
function deleteObject(key, isFolder) {
  askConfirm(isFolder ? 'Xóa thư mục?' : 'Xóa file?', `“${key}” sẽ bị xóa vĩnh viễn khỏi bucket “${objBucket}”.`, async () => {
    try {
      await api(`/api/objects?bucket=${encodeURIComponent(objBucket)}&key=${encodeURIComponent(key)}`, { method: 'DELETE' });
      toast('Đã xóa.');
      loadObjects();
    } catch (e) { toast(e.message, false); }
  });
}

/* ---------- khởi động ---------- */
document.getElementById('themeBtn').addEventListener('click', toggleTheme);
document.getElementById('refreshBtn').addEventListener('click', () => switchTab(currentTab));
document.getElementById('menuBtn').addEventListener('click', () => $('#mobileNav').classList.toggle('hidden'));
document.getElementById('bucketSearch').addEventListener('input', loadBuckets);
initTheme();
buildNav();
switchTab('dashboard');
refreshIcons();
