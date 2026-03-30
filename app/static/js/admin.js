// DVProxy Admin Panel JavaScript

const API_BASE = '';
let token = localStorage.getItem('dvproxy_token');
let charts = {};

// ==================== Authentication ====================

async function checkAuth() {
  if (!token) {
    window.location.href = '/admin/login';
    return false;
  }

  try {
    const response = await api('/admin/verify');
    if (response.valid) {
      document.getElementById('username').textContent = response.username;
      // Set avatar initial
      const avatar = document.getElementById('userAvatar');
      if (avatar) {
        avatar.textContent = response.username.charAt(0).toUpperCase();
      }
      return true;
    }
  } catch (e) {
    console.error('Auth check failed:', e);
  }

  localStorage.removeItem('dvproxy_token');
  window.location.href = '/admin/login';
  return false;
}

// API helper
async function api(endpoint, options = {}) {
  const response = await fetch(API_BASE + endpoint, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });

  if (response.status === 401) {
    localStorage.removeItem('dvproxy_token');
    window.location.href = '/admin/login';
    return;
  }

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'API error');
  }

  return response.json();
}

// ==================== Navigation ====================

function initNavigation() {
  document.querySelectorAll('.nav-link').forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const section = link.dataset.section;
      showSection(section);
      // Update URL hash
      window.location.hash = section;
    });
  });

  // Handle initial hash
  const hash = window.location.hash.slice(1);
  if (hash && document.getElementById(hash)) {
    showSection(hash);
  }
}

function showSection(sectionId) {
  document
    .querySelectorAll('.section')
    .forEach((s) => s.classList.remove('active'));
  document
    .querySelectorAll('.nav-link')
    .forEach((l) => l.classList.remove('active'));

  const section = document.getElementById(sectionId);
  const link = document.querySelector(`[data-section="${sectionId}"]`);

  if (section) section.classList.add('active');
  if (link) link.classList.add('active');

  // Load section data
  if (sectionId === 'dashboard') loadDashboard();
  if (sectionId === 'keys') loadKeys();
  if (sectionId === 'analytics') loadAnalytics();
  if (sectionId === 'deepvlab') loadDeepVLabStatus();
}

function logout() {
  localStorage.removeItem('dvproxy_token');
  window.location.href = '/admin/login';
}

// ==================== Utilities ====================

function formatNumber(num) {
  if (num === null || num === undefined) return '-';
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
  return num.toLocaleString();
}

function formatCurrency(num) {
  if (num === null || num === undefined) return '-';
  return '$' + num.toFixed(4);
}

function formatDate(dateStr) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return (
    date.toLocaleDateString() +
    ' ' +
    date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  );
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function showToast(message, type = 'success') {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 3000);
}

// ==================== Dashboard ====================

async function loadDashboard() {
  try {
    const stats = await api('/admin/stats/global');

    document.getElementById('totalRequests').textContent = formatNumber(
      stats.total_requests,
    );
    document.getElementById('totalTokens').textContent = formatNumber(
      stats.total_input_tokens + stats.total_output_tokens,
    );
    document.getElementById('totalCost').textContent = formatCurrency(
      stats.total_cost_estimate,
    );
    document.getElementById('activeKeys').textContent = stats.active_keys;
    document.getElementById('todayRequests').textContent = formatNumber(
      stats.requests_today,
    );
    document.getElementById('weekRequests').textContent = formatNumber(
      stats.requests_this_week,
    );
    document.getElementById('uniqueIPs').textContent = stats.unique_ips;

    // Load charts
    await Promise.all([
      loadTrendChart(),
      loadModelChart(),
      loadEndpointChart(),
      loadIPTable(),
    ]);
  } catch (e) {
    console.error('Failed to load dashboard:', e);
  }
}

async function loadTrendChart() {
  try {
    const data = await api('/admin/stats/trend?days=30');
    const ctx = document.getElementById('trendChart').getContext('2d');

    if (charts.trend) charts.trend.destroy();

    charts.trend = new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.map((d) => d.date.slice(5)), // MM-DD format
        datasets: [
          {
            label: 'Requests',
            data: data.map((d) => d.requests),
            borderColor: '#6366f1',
            backgroundColor: 'rgba(99, 102, 241, 0.1)',
            fill: true,
            tension: 0.4,
            pointRadius: 0,
            pointHoverRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1e293b',
            titleColor: '#f8fafc',
            bodyColor: '#94a3b8',
            borderColor: '#334155',
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            grid: { color: 'rgba(51, 65, 85, 0.5)' },
            ticks: { color: '#94a3b8', maxTicksLimit: 7 },
          },
          y: {
            grid: { color: 'rgba(51, 65, 85, 0.5)' },
            ticks: { color: '#94a3b8' },
            beginAtZero: true,
          },
        },
        interaction: {
          intersect: false,
          mode: 'index',
        },
      },
    });
  } catch (e) {
    console.error('Failed to load trend chart:', e);
  }
}

async function loadModelChart() {
  try {
    const data = await api('/admin/stats/models?days=30');
    const ctx = document.getElementById('modelChart').getContext('2d');

    if (charts.model) charts.model.destroy();

    const colors = [
      '#6366f1',
      '#10b981',
      '#f59e0b',
      '#ef4444',
      '#8b5cf6',
      '#06b6d4',
      '#ec4899',
    ];

    charts.model = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: data.map((d) => d.model || 'unknown'),
        datasets: [
          {
            data: data.map((d) => d.requests),
            backgroundColor: colors.slice(0, data.length),
            borderWidth: 0,
            hoverOffset: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '65%',
        plugins: {
          legend: {
            position: 'right',
            labels: {
              color: '#94a3b8',
              padding: 12,
              usePointStyle: true,
              pointStyle: 'circle',
            },
          },
        },
      },
    });
  } catch (e) {
    console.error('Failed to load model chart:', e);
  }
}

async function loadEndpointChart() {
  try {
    const data = await api('/admin/stats/endpoints?days=30');
    const ctx = document.getElementById('endpointChart').getContext('2d');

    if (charts.endpoint) charts.endpoint.destroy();

    const colors = ['#6366f1', '#10b981', '#f59e0b'];

    charts.endpoint = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: data.map((d) => d.endpoint || 'unknown'),
        datasets: [
          {
            label: 'Requests',
            data: data.map((d) => d.requests),
            backgroundColor: colors.slice(0, data.length),
            borderRadius: 8,
            maxBarThickness: 50,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#94a3b8' } },
          y: {
            grid: { color: 'rgba(51, 65, 85, 0.5)' },
            ticks: { color: '#94a3b8' },
            beginAtZero: true,
          },
        },
      },
    });
  } catch (e) {
    console.error('Failed to load endpoint chart:', e);
  }
}

async function loadIPTable() {
  try {
    const data = await api('/admin/stats/ips?days=7&limit=10');
    const container = document.getElementById('ipTable');

    if (!data || data.length === 0) {
      container.innerHTML =
        '<div class="empty-state" style="padding: 20px;"><p>No data available</p></div>';
      return;
    }

    container.innerHTML = data
      .map(
        (d) => `
          <div class="row">
            <span class="ip">${escapeHtml(d.ip_address)}</span>
            <span class="count">${formatNumber(d.requests)} requests</span>
          </div>
        `,
      )
      .join('');
  } catch (e) {
    console.error('Failed to load IP table:', e);
  }
}

// ==================== API Keys ====================

async function loadKeys() {
  const tbody = document.getElementById('keysTable');
  tbody.innerHTML =
    '<tr><td colspan="6" class="loading"><span class="spinner"></span> Loading...</td></tr>';

  try {
    const keys = await api('/admin/keys');

    if (keys.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6">
            <div class="empty-state">
              <div class="empty-state-icon">🔑</div>
              <h3>No API Keys</h3>
              <p>Create your first API key to get started.</p>
              <button class="btn btn-primary" onclick="showCreateKeyModal()">Create Key</button>
            </div>
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = keys
      .map(
        (key) => `
          <tr>
            <td>
              <strong>${escapeHtml(key.name)}</strong>
              ${key.description ? `<br><small style="color: var(--text-muted)">${escapeHtml(key.description)}</small>` : ''}
            </td>
            <td>
              <span class="key-preview">${key.key.substring(0, 12)}...</span>
            </td>
            <td>
              ${
                key.quota_limit
                  ? `${formatNumber(key.quota_used)} / ${formatNumber(key.quota_limit)}`
                  : `${formatNumber(key.quota_used)} <small style="color: var(--text-muted)">(unlimited)</small>`
              }
            </td>
            <td>
              <span class="status-badge ${key.is_active ? 'status-active' : 'status-inactive'}">
                ${key.is_active ? 'Active' : 'Inactive'}
              </span>
            </td>
            <td>${key.last_used_at ? formatDate(key.last_used_at) : '<span style="color: var(--text-muted)">Never</span>'}</td>
            <td>
              <div class="actions">
                <button class="btn btn-sm btn-secondary" onclick="editKey(${key.id})">Edit</button>
                <button class="btn btn-sm btn-danger" onclick="deleteKey(${key.id}, '${escapeHtml(key.name)}')">Delete</button>
              </div>
            </td>
          </tr>
        `,
      )
      .join('');

    // Update filter dropdown
    const keyFilter = document.getElementById('keyFilter');
    if (keyFilter) {
      keyFilter.innerHTML =
        '<option value="">All Keys</option>' +
        keys
          .map((k) => `<option value="${k.id}">${escapeHtml(k.name)}</option>`)
          .join('');
    }
  } catch (e) {
    console.error('Failed to load keys:', e);
    tbody.innerHTML =
      '<tr><td colspan="6" class="loading" style="color: var(--danger);">Failed to load keys</td></tr>';
  }
}

function showCreateKeyModal() {
  document.getElementById('keyModalTitle').textContent = 'Create API Key';
  document.getElementById('keyId').value = '';
  document.getElementById('keyName').value = '';
  document.getElementById('keyDescription').value = '';
  document.getElementById('quotaLimit').value = '';
  document.getElementById('rateLimit').value = '60';
  document.getElementById('keyActive').checked = true;
  document.getElementById('keyModal').classList.remove('hidden');
  document.getElementById('keyName').focus();
}

async function editKey(keyId) {
  try {
    const key = await api(`/admin/keys/${keyId}`);

    document.getElementById('keyModalTitle').textContent = 'Edit API Key';
    document.getElementById('keyId').value = key.id;
    document.getElementById('keyName').value = key.name;
    document.getElementById('keyDescription').value = key.description || '';
    document.getElementById('quotaLimit').value = key.quota_limit || '';
    document.getElementById('rateLimit').value = key.rate_limit;
    document.getElementById('keyActive').checked = key.is_active;
    document.getElementById('keyModal').classList.remove('hidden');
  } catch (e) {
    showToast('Failed to load key: ' + e.message, 'error');
  }
}

function closeKeyModal() {
  document.getElementById('keyModal').classList.add('hidden');
}

document.getElementById('keyForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  const keyId = document.getElementById('keyId').value;
  const data = {
    name: document.getElementById('keyName').value.trim(),
    description: document.getElementById('keyDescription').value.trim() || null,
    quota_limit: document.getElementById('quotaLimit').value
      ? parseInt(document.getElementById('quotaLimit').value)
      : null,
    rate_limit: parseInt(document.getElementById('rateLimit').value) || 60,
    is_active: document.getElementById('keyActive').checked,
  };

  try {
    if (keyId) {
      await api(`/admin/keys/${keyId}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
      showToast('API key updated successfully');
    } else {
      const result = await api('/admin/keys', {
        method: 'POST',
        body: JSON.stringify(data),
      });

      // Show the new key
      document.getElementById('newKeyValue').textContent = result.key;
      document.getElementById('keyCreatedModal').classList.remove('hidden');
    }

    closeKeyModal();
    loadKeys();
  } catch (e) {
    showToast('Failed to save key: ' + e.message, 'error');
  }
});

async function deleteKey(keyId, keyName) {
  if (
    !confirm(
      `Are you sure you want to delete "${keyName}"?\n\nThis action cannot be undone.`,
    )
  ) {
    return;
  }

  try {
    await api(`/admin/keys/${keyId}`, { method: 'DELETE' });
    showToast('API key deleted');
    loadKeys();
  } catch (e) {
    showToast('Failed to delete key: ' + e.message, 'error');
  }
}

function closeKeyCreatedModal() {
  document.getElementById('keyCreatedModal').classList.add('hidden');
}

function copyKey() {
  const key = document.getElementById('newKeyValue').textContent;
  navigator.clipboard.writeText(key).then(() => {
    showToast('Key copied to clipboard!');
  });
}

// ==================== Analytics ====================

async function loadAnalytics() {
  const days = document.getElementById('daysFilter').value;
  const keyId = document.getElementById('keyFilter').value;

  const params = new URLSearchParams({ days });
  if (keyId) params.append('key_id', keyId);

  try {
    const [trendData, modelData] = await Promise.all([
      api(`/admin/stats/trend?${params}`),
      api(`/admin/stats/models?${params}`),
    ]);

    loadDetailedTrendChart(trendData);
    loadModelTokensChart(modelData);
    loadModelCostChart(modelData);
  } catch (e) {
    console.error('Failed to load analytics:', e);
  }
}

function loadDetailedTrendChart(data) {
  const ctx = document.getElementById('detailedTrendChart').getContext('2d');

  if (charts.detailedTrend) charts.detailedTrend.destroy();

  charts.detailedTrend = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map((d) => d.date.slice(5)),
      datasets: [
        {
          label: 'Requests',
          data: data.map((d) => d.requests),
          borderColor: '#6366f1',
          backgroundColor: 'rgba(99, 102, 241, 0.1)',
          fill: true,
          yAxisID: 'y',
          tension: 0.4,
          pointRadius: 2,
        },
        {
          label: 'Input Tokens',
          data: data.map((d) => d.input_tokens),
          borderColor: '#10b981',
          borderDash: [5, 5],
          yAxisID: 'y1',
          tension: 0.4,
          pointRadius: 0,
        },
        {
          label: 'Output Tokens',
          data: data.map((d) => d.output_tokens),
          borderColor: '#f59e0b',
          borderDash: [5, 5],
          yAxisID: 'y1',
          tension: 0.4,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          grid: { color: 'rgba(51, 65, 85, 0.5)' },
          ticks: { color: '#94a3b8', maxTicksLimit: 10 },
        },
        y: {
          type: 'linear',
          position: 'left',
          grid: { color: 'rgba(51, 65, 85, 0.5)' },
          ticks: { color: '#94a3b8' },
          title: { display: true, text: 'Requests', color: '#94a3b8' },
          beginAtZero: true,
        },
        y1: {
          type: 'linear',
          position: 'right',
          grid: { display: false },
          ticks: { color: '#94a3b8' },
          title: { display: true, text: 'Tokens', color: '#94a3b8' },
          beginAtZero: true,
        },
      },
      plugins: {
        legend: {
          labels: { color: '#94a3b8', usePointStyle: true },
          position: 'top',
        },
        tooltip: {
          backgroundColor: '#1e293b',
          titleColor: '#f8fafc',
          bodyColor: '#94a3b8',
          borderColor: '#334155',
          borderWidth: 1,
        },
      },
    },
  });
}

function loadModelTokensChart(data) {
  const ctx = document.getElementById('modelTokensChart').getContext('2d');

  if (charts.modelTokens) charts.modelTokens.destroy();

  charts.modelTokens = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.map((d) => d.model || 'unknown'),
      datasets: [
        {
          label: 'Input Tokens',
          data: data.map((d) => d.input_tokens),
          backgroundColor: '#6366f1',
          borderRadius: 4,
        },
        {
          label: 'Output Tokens',
          data: data.map((d) => d.output_tokens),
          backgroundColor: '#10b981',
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { grid: { display: false }, ticks: { color: '#94a3b8' } },
        y: {
          grid: { color: 'rgba(51, 65, 85, 0.5)' },
          ticks: { color: '#94a3b8' },
          beginAtZero: true,
        },
      },
      plugins: {
        legend: { labels: { color: '#94a3b8' } },
      },
    },
  });
}

function loadModelCostChart(data) {
  const ctx = document.getElementById('modelCostChart').getContext('2d');

  if (charts.modelCost) charts.modelCost.destroy();

  const colors = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

  charts.modelCost = new Chart(ctx, {
    type: 'pie',
    data: {
      labels: data.map((d) => d.model || 'unknown'),
      datasets: [
        {
          data: data.map((d) => d.cost_estimate),
          backgroundColor: colors.slice(0, data.length),
          borderWidth: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: { color: '#94a3b8', usePointStyle: true },
        },
        tooltip: {
          callbacks: {
            label: function (context) {
              return `${context.label}: $${context.raw.toFixed(4)}`;
            },
          },
        },
      },
    },
  });
}

function loadKeyStats() {
  loadAnalytics();
}

// ==================== Multi-Method Login ====================

const DEEPVLAB_AUTH_URL = 'https://accounts.deepvlab.ai/login';
const CALLBACK_PORT = 7863;

async function loadDeepVLabStatus() {
  // Check available login methods
  try {
    const methods = await api('/admin/login-methods');
    if (methods.methods.feishu) {
      document.getElementById('feishuTabBtn').style.display = 'inline-block';
    }
  } catch (e) {
    console.error('Failed to load login methods:', e);
    // Show Feishu tab even if check fails (force enable)
    document.getElementById('feishuTabBtn').style.display = 'inline-block';
  }

  // Check account status
  try {
    const status = await api('/admin/deepvlab/status');
    if (status.logged_in) {
      showAccountLinked(status.user, status.login_method || 'deepvlab');
    }
  } catch (e) {
    console.error('Failed to load status:', e);
  }
}

function showLoginTab(method) {
  // Update tab buttons
  document
    .querySelectorAll('.tab-btn')
    .forEach((btn) => btn.classList.remove('active'));
  event.target.classList.add('active');

  // Hide all login tabs
  document
    .querySelectorAll('.login-tab')
    .forEach((tab) => (tab.style.display = 'none'));

  // Show selected tab
  document.getElementById(`${method}LoginTab`).style.display = 'block';
}

function showAccountLinked(user, method) {
  document.getElementById('loginMethodsContainer').style.display = 'none';
  document.getElementById('deepvlabUserInfo').style.display = 'block';

  const methodNames = {
    deepvlab: 'DeepVLab OAuth',
    feishu: 'Feishu OAuth',
    cheetah_oa: 'Cheetah OA',
  };

  document.getElementById('dvLoginMethod').textContent =
    methodNames[method] || method;
  document.getElementById('dvUserId').textContent =
    user.user_id || user.userId || '-';
  document.getElementById('dvUserName').textContent = user.name || '-';
  document.getElementById('dvUserEmail').textContent = user.email || '-';

  // Store in localStorage for UI persistence
  localStorage.setItem(
    'dvproxy_deepvlab_credentials',
    JSON.stringify({ ...user, login_method: method }),
  );
}

async function generateAuthUrl(method) {
  if (method === 'deepvlab') {
    const redirectUri = `http://localhost:${CALLBACK_PORT}/callback?plat=deepvlab`;
    const authUrl = `${DEEPVLAB_AUTH_URL}?redirect_to=${encodeURIComponent(redirectUri)}&redirect_mode=same_window`;

    document.getElementById('authUrl').value = authUrl;
    document.getElementById('deepvlabStep2').style.display = 'block';
    document.getElementById('deepvlabStep3').style.display = 'block';

    showToast('Authorization URL generated!');
  } else if (method === 'feishu') {
    const btn = document.getElementById('feishuGenerateBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Generating...';

    try {
      const response = await api('/admin/feishu/auth-url');
      if (response.success) {
        if (response.auth_url) {
          // Auto-generated URL
          document.getElementById('feishuAuthUrl').value = response.auth_url;
          document.getElementById('feishuStep2').style.display = 'block';
          document.getElementById('feishuStep3').style.display = 'block';
          showToast('Feishu authorization URL generated!');
        } else if (response.manual_mode) {
          // Manual mode - show instructions
          document.getElementById('feishuManualInstructions').textContent =
            response.manual_instructions;
          document.getElementById('feishuManualMode').style.display = 'block';
          document.getElementById('feishuStep3').style.display = 'block';
          showToast('Manual mode - follow instructions below');
        }
      } else {
        showToast(response.error || 'Failed to generate Feishu URL', 'error');
      }
    } catch (e) {
      showToast('Failed to generate Feishu URL: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML =
        '<img src="https://res.ainirobot.com/orics/down/v2_k005_20250904_c768e6a4/feishu.ico" alt="" style="width: 16px; height: 16px; margin-right: 6px; vertical-align: middle;">Generate Feishu URL';
    }
  }
}

function copyAuthUrl() {
  const url = document.getElementById('authUrl').value;
  navigator.clipboard.writeText(url).then(() => {
    showToast('URL copied to clipboard!');
  });
}

function openAuthUrl() {
  const url = document.getElementById('authUrl').value;
  window.open(url, '_blank');
}

function copyFeishuAuthUrl() {
  const url = document.getElementById('feishuAuthUrl').value;
  navigator.clipboard.writeText(url).then(() => {
    showToast('URL copied to clipboard!');
  });
}

function openFeishuAuthUrl() {
  const url = document.getElementById('feishuAuthUrl').value;
  window.open(url, '_blank');
}

async function processCallback(method) {
  if (method === 'deepvlab') {
    await processDeepVLabCallback();
  } else if (method === 'feishu') {
    await processFeishuCallback();
  }
}

async function processDeepVLabCallback() {
  const callbackUrl = document.getElementById('callbackUrl').value.trim();
  const processBtn = document.getElementById('processCallbackBtn');

  if (!callbackUrl) {
    showToast('Please paste the callback URL', 'error');
    return;
  }

  try {
    const url = new URL(callbackUrl);
    const token = url.searchParams.get('token');
    const userId = url.searchParams.get('user_id');

    if (!token || !userId) {
      showToast(
        'Invalid callback URL. Make sure it contains token and user_id parameters.',
        'error',
      );
      return;
    }

    processBtn.disabled = true;
    processBtn.innerHTML = '<span class="spinner"></span> Processing...';

    const response = await api('/admin/deepvlab/login', {
      method: 'POST',
      body: JSON.stringify({ token, user_id: userId }),
    });

    if (response.success) {
      showAccountLinked(
        {
          user_id: response.user_id,
          name: response.name,
          email: response.email,
        },
        'deepvlab',
      );
      showToast('DeepVLab account linked successfully!');
      document.getElementById('callbackUrl').value = '';
    } else {
      showToast(response.error || 'Failed to process callback', 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    processBtn.disabled = false;
    processBtn.innerHTML = 'Complete Login';
  }
}

async function processFeishuCallback() {
  const callbackUrl = document.getElementById('feishuCallbackUrl').value.trim();
  const processBtn = document.getElementById('feishuProcessBtn');

  if (!callbackUrl) {
    showToast('Please paste the callback URL', 'error');
    return;
  }

  try {
    const url = new URL(callbackUrl);
    const code = url.searchParams.get('code');

    if (!code) {
      showToast(
        'Invalid callback URL. Make sure it contains a code parameter.',
        'error',
      );
      return;
    }

    processBtn.disabled = true;
    processBtn.innerHTML = '<span class="spinner"></span> Processing...';

    const response = await api('/admin/feishu/login', {
      method: 'POST',
      body: JSON.stringify({
        code,
        redirect_uri: `http://localhost:${CALLBACK_PORT}/callback`,
      }),
    });

    if (response.success) {
      showAccountLinked(
        {
          user_id: response.user_id,
          name: response.name,
          email: response.email,
        },
        'feishu',
      );
      showToast('Feishu account linked successfully!');
      document.getElementById('feishuCallbackUrl').value = '';
    } else {
      showToast(response.error || 'Failed to process Feishu callback', 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    processBtn.disabled = false;
    processBtn.innerHTML = 'Complete Login';
  }
}

async function handleCheetahLogin(event) {
  event.preventDefault();

  const email = document.getElementById('cheetahEmail').value.trim();
  const password = document.getElementById('cheetahPassword').value;
  const loginBtn = document.getElementById('cheetahLoginBtn');

  if (!email || !password) {
    showToast('Please enter email and password', 'error');
    return;
  }

  loginBtn.disabled = true;
  loginBtn.innerHTML = '<span class="spinner"></span> Logging in...';

  try {
    const response = await api('/admin/cheetah/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });

    if (response.success) {
      showAccountLinked(
        {
          user_id: response.user_id,
          name: response.name,
          email: response.email,
        },
        'cheetah_oa',
      );
      showToast('Cheetah OA login successful!');
      document.getElementById('cheetahPassword').value = '';
    } else {
      showToast(
        response.error || 'Login failed. Please check your credentials.',
        'error',
      );
    }
  } catch (e) {
    showToast('Login failed: ' + e.message, 'error');
  } finally {
    loginBtn.disabled = false;
    loginBtn.innerHTML = 'Login with OA';
  }
}

async function unlinkAccount() {
  if (!confirm('Are you sure you want to unlink your account?')) {
    return;
  }

  try {
    await api('/admin/deepvlab/logout', { method: 'POST' });
    localStorage.removeItem('dvproxy_deepvlab_credentials');

    document.getElementById('deepvlabUserInfo').style.display = 'none';
    document.getElementById('loginMethodsContainer').style.display = 'block';

    // Reset all login forms
    document.getElementById('deepvlabStep2').style.display = 'none';
    document.getElementById('deepvlabStep3').style.display = 'none';
    document.getElementById('feishuStep2').style.display = 'none';
    document.getElementById('feishuStep3').style.display = 'none';

    showToast('Account unlinked');
  } catch (e) {
    showToast('Failed to unlink account: ' + e.message, 'error');
  }
}

// ==================== Event Handlers ====================

// Close modals on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeKeyModal();
    closeKeyCreatedModal();
  }
});

// Close modals on backdrop click
document.querySelectorAll('.modal').forEach((modal) => {
  modal.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
      modal.classList.add('hidden');
    }
  });
});

// ==================== Initialize ====================

document.addEventListener('DOMContentLoaded', async () => {
  if (await checkAuth()) {
    initNavigation();
    loadDashboard();
  }
});
