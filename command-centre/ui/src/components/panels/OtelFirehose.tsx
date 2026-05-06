// Live SSE tail of /api/firehose. Pause/resume, filter by event_name.
import { useEffect, useRef, useState } from 'react';
import { Pause, Play, Trash2 } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { StatePill } from '@/components/ui/StatePill';
import { Select } from '@/components/ui/Field';
import { firehoseUrl } from '@/lib/api';
import type { FirehoseEvent } from '@/lib/types';
import { fmtMs } from '@/lib/format';

const MAX_BUFFER = 400;

type Status = 'connecting' | 'streaming' | 'paused' | 'error';

export function OtelFirehose() {
  const [events, setEvents] = useState<FirehoseEvent[]>([]);
  const [status, setStatus] = useState<Status>('connecting');
  const [filter, setFilter] = useState<string>('');
  const [paused, setPaused] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const pausedRef = useRef(false);
  pausedRef.current = paused;

  useEffect(() => {
    const url = firehoseUrl(filter || undefined);
    const es = new EventSource(url);
    esRef.current = es;
    setStatus('connecting');
    setEvents([]);

    es.onmessage = (msg) => {
      if (pausedRef.current) return;
      try {
        const row = JSON.parse(msg.data) as FirehoseEvent;
        setEvents(prev => {
          const next = [...prev, row];
          return next.length > MAX_BUFFER ? next.slice(-MAX_BUFFER) : next;
        });
        setStatus('streaming');
      } catch { /* swallow malformed frames */ }
    };
    es.addEventListener('ready', () => setStatus('streaming'));
    es.addEventListener('error', () => setStatus('error'));
    es.onerror = () => setStatus('error');

    return () => { es.close(); };
  }, [filter]);

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>OTEL firehose</Kicker>
          <CardTitle>Live event stream</CardTitle>
          <CardDescription>
            Server-Sent Events from <span className="font-mono">/api/firehose</span>. Latest {MAX_BUFFER} retained.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <StatePill tone={status === 'streaming' ? 'info' : status === 'error' ? 'error' : 'idle'}>
            {paused ? 'paused' : status}
          </StatePill>
          <Select
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="!h-8 !text-[12px]"
          >
            <option value="">all events</option>
            <option value="tool_call">tool_call</option>
            <option value="session_start">session_start</option>
            <option value="session_stop">session_stop</option>
            <option value="api_error">api_error</option>
            <option value="api_request">api_request</option>
          </Select>
          <Button
            size="sm" variant="ghost"
            leftIcon={paused ? <Play size={12} /> : <Pause size={12} />}
            onClick={() => setPaused(p => !p)}
          >
            {paused ? 'resume' : 'pause'}
          </Button>
          <Button size="sm" variant="ghost" leftIcon={<Trash2 size={12} />} onClick={() => setEvents([])}>
            clear
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="bg-surface border border-border rounded-lg font-mono text-[11.5px] h-[420px] overflow-y-auto p-2">
          {events.length === 0 ? (
            <div className="text-text-subtle text-center py-8">
              {status === 'connecting' ? 'connecting…' : 'no events yet'}
            </div>
          ) : (
            events.slice().reverse().map((e, i) => <EventRow key={`${e.id}-${i}`} e={e} />)
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function EventRow({ e }: { e: FirehoseEvent }) {
  const ts = e.timestamp?.slice(11, 19) ?? '—';
  const errored = !!e.error_message;
  return (
    <div className={`py-1 px-2 rounded flex items-center gap-2 ${errored ? 'text-status-red' : ''}`}>
      <span className="text-text-subtle w-[64px] shrink-0 tabular-nums">{ts}</span>
      <Badge tone={
        errored ? 'red' :
        e.event_name === 'tool_call' ? 'cyan' :
        e.event_name === 'session_start' ? 'blue' :
        e.event_name === 'session_stop' ? 'purple' :
        'neutral'
      }>{e.event_name ?? 'event'}</Badge>
      {e.tool_name && <span className="text-text">{e.tool_name}</span>}
      {e.mcp_server_name && (
        <span className="text-text-dim">{e.mcp_server_name}/{e.mcp_tool_name ?? ''}</span>
      )}
      {e.model && <span className="text-text-dim">{e.model}</span>}
      {e.tool_duration_ms != null && (
        <span className="text-text-subtle">{fmtMs(e.tool_duration_ms)}</span>
      )}
      {e.session_id && (
        <span className="text-text-subtle ml-auto truncate">sess {e.session_id.slice(0, 8)}</span>
      )}
      {e.error_message && (
        <span className="text-status-red/80 truncate max-w-[260px]" title={e.error_message}>{e.error_message}</span>
      )}
    </div>
  );
}
