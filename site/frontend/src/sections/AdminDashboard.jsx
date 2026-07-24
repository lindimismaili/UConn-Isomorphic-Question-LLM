import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  AppShell,
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  DataTable,
  EmptyState,
  KeyValueTable,
  Modal,
  Tabs,
  TextInput,
  ToggleSwitch,
} from '../components/ui/index.js';
import { SiteFooter, SiteHeader } from './SiteChrome.jsx';
import { requestJson } from '../utils/api.js';

const ROOTS = [
  { id: 'archive', label: 'Archive' },
  { id: 'uploads', label: 'Uploads' },
  { id: 'outputs', label: 'Generation outputs' },
  { id: 'evaluations', label: 'Evaluation sources' },
];

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'files', label: 'Files' },
  { id: 'workflows', label: 'Workflows & Evals' },
  { id: 'operations', label: 'Operations' },
  { id: 'settings', label: 'Settings' },
];

function formatBytes(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = Number(value);
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function time(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function SummaryCard({ label, value, detail, tone = 'neutral' }) {
  return (
    <Card className="admin-summary-card">
      <CardBody>
        <span className="admin-summary-label">{label}</span>
        <strong className="admin-summary-value">{value}</strong>
        <Chip tone={tone} shape="square">{detail}</Chip>
      </CardBody>
    </Card>
  );
}

function FileBrowser({ onError }) {
  const [root, setRoot] = useState('archive');
  const [path, setPath] = useState('');
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ path }).toString();
      const result = await requestJson(`/api/admin/files/${root}/entries?${query}`, { fallbackError: 'Could not load files' });
      setEntries(result.entries || []);
    } catch (err) {
      onError(err.message);
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [onError, path, root]);

  useEffect(() => { load(); }, [load]);

  const openEntry = (entry) => {
    if (entry.isDirectory) setPath(entry.path);
  };
  const goUp = () => setPath((current) => current.split('/').slice(0, -1).join('/'));
  const download = (entry) => window.open(`/api/admin/files/${root}/download?path=${encodeURIComponent(entry.path)}`, '_blank', 'noopener,noreferrer');

  return (
    <Card>
      <CardHeader title="Internal files" subtitle="Only approved data roots are available; deployment configuration and credentials stay hidden." />
      <CardBody className="admin-stack">
        <div className="admin-action-row">
          {ROOTS.map((item) => (
            <Button key={item.id} variant={root === item.id ? 'primary' : 'secondary'} size="sm" onClick={() => { setRoot(item.id); setPath(''); }}>
              {item.label}
            </Button>
          ))}
        </div>
        <div className="admin-file-toolbar">
          <span className="admin-path">/{ROOTS.find((item) => item.id === root)?.label}{path ? `/${path}` : ''}</span>
          <div className="admin-action-row">
            <Button variant="ghost" size="sm" disabled={!path} onClick={goUp}>Up</Button>
            <Button variant="secondary" size="sm" onClick={() => window.open(`/api/admin/files/${root}/zip?path=${encodeURIComponent(path)}`, '_blank', 'noopener,noreferrer')}>Download ZIP</Button>
          </div>
        </div>
        <div className="admin-table-shell">
          <DataTable>
            <thead><tr><th>Name</th><th>Modified</th><th>Size</th><th>Actions</th></tr></thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.path}>
                  <td>{entry.isDirectory ? <Button variant="ghost" size="sm" onClick={() => openEntry(entry)}>{entry.name}/</Button> : entry.name}</td>
                  <td>{time(entry.modifiedAt)}</td><td>{entry.isDirectory ? 'Folder' : formatBytes(entry.size)}</td>
                  <td>{entry.isDirectory ? <Button variant="ghost" size="sm" onClick={() => openEntry(entry)}>Browse</Button> : <div className="admin-action-row"><Button variant="ghost" size="sm" onClick={() => window.open(`/api/admin/files/${root}/preview?path=${encodeURIComponent(entry.path)}`, '_blank', 'noopener,noreferrer')}>View</Button><Button variant="secondary" size="sm" onClick={() => download(entry)}>Download</Button></div>}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </div>
        {!loading && entries.length === 0 ? <EmptyState title="No files in this folder" /> : null}
      </CardBody>
    </Card>
  );
}

export default function AdminDashboard() {
  const [tab, setTab] = useState('overview');
  const [data, setData] = useState(null);
  const [session, setSession] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [maintenanceConfirm, setMaintenanceConfirm] = useState(null);
  const [apiKey, setApiKey] = useState('');
  const [keyStatus, setKeyStatus] = useState('');

  const load = useCallback(async () => {
    try {
      const [nextSession, nextData] = await Promise.all([
        requestJson('/api/admin/session', { fallbackError: 'Admin authentication failed' }),
        requestJson('/api/admin/dashboard', { fallbackError: 'Could not load the operations dashboard' }),
      ]);
      setSession(nextSession);
      setData(nextData);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const interval = window.setInterval(load, 30_000);
    return () => window.clearInterval(interval);
  }, [load]);

  const updateSettings = async (payload) => {
    try {
      await requestJson('/api/admin/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), fallbackError: 'Could not save setting' });
      await load();
    } catch (err) { setError(err.message); }
  };

  const submitKey = async () => {
    setKeyStatus('Validating key…');
    try {
      await requestJson('/api/admin/openai-key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: apiKey }), fallbackError: 'Could not update the OpenAI key' });
      setApiKey('');
      setKeyStatus('Key validated and applied.');
      await load();
    } catch (err) { setKeyStatus(err.message); }
  };

  const maintenance = data?.maintenance;
  const storageCards = useMemo(() => Object.entries(data?.storage || {}), [data]);
  const content = (() => {
    if (!data) return <Card><CardBody><EmptyState title={loading ? 'Loading operations dashboard…' : 'Dashboard unavailable'} /></CardBody></Card>;
    if (tab === 'files') return <FileBrowser onError={setError} />;
    if (tab === 'workflows') return <Card><CardHeader title="Workflow and evaluation activity" subtitle="Durable history with current status and output folders." /><CardBody><div className="admin-table-shell"><DataTable><thead><tr><th>Workflow</th><th>Status</th><th>Updated</th><th>Output</th><th>Error</th></tr></thead><tbody>{data.workflows.map((workflow) => <tr key={workflow.id}><td><Chip mono>{workflow.id.slice(0, 12)}</Chip></td><td><Chip tone={workflow.status === 'completed' ? 'success' : workflow.status === 'errored' ? 'error' : 'warning'}>{workflow.status}</Chip></td><td>{time(workflow.updatedAt)}</td><td>{workflow.outputFolder || '—'}</td><td>{workflow.error || '—'}</td></tr>)}</tbody></DataTable></div></CardBody></Card>;
    if (tab === 'operations') return <div className="admin-grid"><Card><CardHeader title="Pi and Cloudflare probes" subtitle="Sanitized status written by the Pi-side operations timer." /><CardBody><KeyValueTable rows={Object.entries(data.operations || {}).filter(([, value]) => typeof value !== 'object').map(([key, value]) => ({ key, value: String(value || '—') }))} /></CardBody></Card><Card><CardHeader title="Recent incidents" /><CardBody className="admin-stack">{data.incidents.length ? data.incidents.map((incident) => <div className="admin-incident" key={incident.id}><Chip tone={incident.status === 'open' ? 'error' : 'success'}>{incident.code}</Chip><strong>{incident.title}</strong><span>{incident.detail}</span><small>{time(incident.updated_at)} · {incident.occurrences} occurrence(s)</small></div>) : <EmptyState title="No recorded incidents" />}</CardBody></Card><Card><CardHeader title="Sanitized service logs" subtitle="Latest bounded excerpts from the backend and Cloudflare tunnel services." /><CardBody className="admin-stack">{Object.entries(data.operations?.logs || {}).map(([name, lines]) => <div className="admin-log-report" key={name}><strong>{name}</strong><pre>{Array.isArray(lines) && lines.length ? lines.join('\n') : 'No report available yet.'}</pre></div>)}</CardBody></Card><Card><CardHeader title="Audit events" /><CardBody><div className="admin-table-shell"><DataTable><thead><tr><th>When</th><th>Admin</th><th>Action</th><th>Outcome</th></tr></thead><tbody>{data.audit.map((event, index) => <tr key={`${event.at}-${index}`}><td>{time(event.at)}</td><td>{event.actor}</td><td>{event.action} · {event.target}</td><td>{event.outcome}</td></tr>)}</tbody></DataTable></div></CardBody></Card></div>;
    if (tab === 'settings') return <div className="admin-grid"><Card><CardHeader title="Availability controls" subtitle="A maintenance change requires confirmation and is recorded in the audit history." /><CardBody className="admin-stack"><ToggleSwitch checked={Boolean(maintenance?.maintenance)} onChange={(checked) => setMaintenanceConfirm(checked)} label={maintenance?.maintenance ? 'Maintenance mode is active' : 'Maintenance mode is inactive'} /><ToggleSwitch checked={Boolean(data.settings.stageCacheEnabled)} onChange={(checked) => updateSettings({ stage_cache_enabled: checked })} label="Enable exact-match stage cache" /><TextInput aria-label="OpenAI cost warning threshold in USD" placeholder="OpenAI cost warning threshold (USD)" type="number" value={data.settings.costWarningDollars ?? ''} onChange={(event) => updateSettings({ cost_warning_dollars: Number(event.target.value || 0) })} /></CardBody></Card><Card><CardHeader title="OpenAI application key" subtitle="The existing key is never shown. A replacement is verified before it is saved." /><CardBody className="admin-stack"><TextInput aria-label="New OpenAI API key" placeholder="New OpenAI API key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /><Button variant="primary" disabled={!apiKey} onClick={submitKey}>Validate and apply key</Button>{keyStatus ? <Chip tone={keyStatus.includes('applied') ? 'success' : 'error'}>{keyStatus}</Chip> : null}</CardBody></Card></div>;
    return <><div className="admin-summary-grid"><SummaryCard label="Service" value={maintenance?.maintenance ? 'Maintenance' : 'Available'} detail={maintenance?.maintenance ? maintenance.code : 'Public workflows enabled'} tone={maintenance?.maintenance ? 'error' : 'success'} /><SummaryCard label="Active workflows" value={data.activeWorkflows} detail="Durable workflow state" tone="primary" /><SummaryCard label="Connected visitors" value={data.activeVisitors} detail="Approx. last 5 minutes" tone="primary" /><SummaryCard label="OpenAI daily cost" value={data.cost?.available ? `${data.cost.amount} ${data.cost.currency}` : 'Unavailable'} detail={data.cost?.configured ? `Checked ${time(data.cost.checked_at)}` : 'Admin cost key not configured'} tone={data.cost?.available ? 'primary' : 'warning'} /><SummaryCard label="Free disk" value={formatBytes(data.disk.free)} detail="Pi output volume" tone="neutral" /></div><div className="admin-grid"><Card><CardHeader title="Storage" /><CardBody><KeyValueTable rows={storageCards.map(([key, value]) => ({ key, value: formatBytes(value) }))} /></CardBody></Card><Card><CardHeader title="Latest incidents" /><CardBody className="admin-stack">{data.incidents.length ? data.incidents.slice(0, 4).map((incident) => <div className="admin-incident" key={incident.id}><Chip tone={incident.status === 'open' ? 'error' : 'success'}>{incident.code}</Chip><span>{time(incident.updated_at)}</span></div>) : <EmptyState title="No incidents" />}</CardBody></Card></div></>;
  })();

  return <AppShell header={<SiteHeader title="Operations Dashboard" />} footer={<SiteFooter version={session ? `Signed in as ${session.email}` : 'Admin access required'} />}><div className="admin-dashboard"><div className="admin-page-heading"><div><h2>Operations dashboard</h2><p>Secure internal controls, health, files, and workflow data.</p></div><Button variant="secondary" size="sm" onClick={load}>Refresh</Button></div>{error ? <Card variant="failed"><CardBody>{error}</CardBody></Card> : null}<Tabs value={tab} onChange={setTab} tabs={TABS} />{content}</div><Modal isOpen={maintenanceConfirm !== null} onClose={() => setMaintenanceConfirm(null)} title={maintenanceConfirm ? 'Enable maintenance mode?' : 'Restore public service?'} footer={<><Button variant="secondary" onClick={() => setMaintenanceConfirm(null)}>Cancel</Button><Button variant={maintenanceConfirm ? 'danger' : 'primary'} onClick={() => { updateSettings({ maintenance: maintenanceConfirm, maintenance_message: 'The site is temporarily unavailable for maintenance.' }); setMaintenanceConfirm(null); }}>{maintenanceConfirm ? 'Enable maintenance' : 'Restore service'}</Button></>}><p>{maintenanceConfirm ? 'New uploads and workflow starts will immediately be blocked with a timestamped public maintenance notice.' : 'Confirm that the underlying issue is resolved before allowing new workflows.'}</p></Modal></AppShell>;
}
