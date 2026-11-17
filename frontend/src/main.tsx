import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

function App() {
  const [status, setStatus] = useState('Checking API readiness…');
  useEffect(() => { fetch('/api/v1/health/ready').then(async r => {
    if (!r.ok) throw new Error();
    const data = await r.json(); setStatus(`API ready · ${data.provider} provider`);
  }).catch(() => setStatus('API unavailable. Check backend health.')); }, []);
  return <main className="mx-auto max-w-3xl p-10"><p className="text-cyan-300">SECURITY OPERATIONS</p>
    <h1 className="my-4 text-4xl font-semibold">SecOps AI</h1>
    <p className="my-6 rounded border border-slate-700 p-4" role="status">{status}</p>
    <h2 className="text-xl">Data and retrieval foundation</h2>
    <p className="my-4 text-slate-300">Authenticate, ingest synthetic security events and policies, and query cited knowledge through the API. Investigation workflows are planned for the next phase.</p>
    <a className="text-cyan-300 underline" href="http://localhost:8000/docs">Open interactive API documentation</a>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App />);
