// Red "Emergency Stop" header button with confirm. Visible on every page.
import { AlertOctagon, Loader2, ShieldOff } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Sheet } from '@/components/ui/Sheet';
import { useEmergencyResume, useEmergencyStop } from '@/hooks/useQueries';
import { StatePill } from '@/components/ui/StatePill';

export function EmergencyStopBanner() {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const stop = useEmergencyStop();
  const resume = useEmergencyResume();

  const stopped = stop.data?.stopped === true && !resume.isSuccess;

  return (
    <div
      className={`rounded-xl border px-4 py-2.5 flex items-center gap-3 transition-colors ${
        stopped
          ? 'bg-status-red/15 border-status-red/40'
          : 'bg-surface/60 border-border'
      }`}
    >
      <span className={stopped ? 'text-status-red' : 'text-text-dim'}>
        <AlertOctagon size={16} />
      </span>
      <div className="flex-1 text-[13px]">
        {stopped ? (
          <>
            <span className="text-status-red font-medium">EMERGENCY STOP engaged.</span>{' '}
            <span className="text-text-dim">
              {stop.data?.processes_killed ?? 0} dispatched children killed.
              Interactive sessions untouched.
            </span>
          </>
        ) : (
          <span className="text-text-dim">
            One-button kill for dispatcher-launched <span className="font-mono">claude -p</span>{' '}
            children. Interactive sessions are always spared.
          </span>
        )}
      </div>
      {stopped ? (
        <>
          <StatePill tone="error">stopped</StatePill>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={resume.isPending ? <Loader2 className="animate-spin" size={14} /> : undefined}
            onClick={() => resume.mutate()}
          >
            Resume
          </Button>
        </>
      ) : (
        <Button
          variant="danger"
          size="sm"
          leftIcon={<ShieldOff size={14} />}
          onClick={() => setConfirmOpen(true)}
          data-action="emergency-stop"
        >
          Emergency stop
        </Button>
      )}
      <Sheet open={confirmOpen} onClose={() => setConfirmOpen(false)} title="Confirm emergency stop" widthClass="w-[420px]">
        <div className="p-5 space-y-4 text-[13.5px]">
          <p>This will SIGTERM every <span className="font-mono">claude -p</span> child that the dispatcher spawned.</p>
          <p className="text-text-dim">
            Interactive sessions running in a separate terminal or the desktop app are identified by PID marker files and will NOT be killed.
          </p>
          <p className="text-text-subtle font-mono text-[12px]">
            In this API-surface build, processes_killed will be 0 until Mission Control ships PID marker files.
          </p>
          <div className="pt-2 flex items-center justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button
              variant="danger"
              leftIcon={stop.isPending ? <Loader2 className="animate-spin" size={14} /> : <ShieldOff size={14} />}
              onClick={() => {
                stop.mutate(undefined, { onSuccess: () => setConfirmOpen(false) });
              }}
            >
              Stop everything
            </Button>
          </div>
        </div>
      </Sheet>
    </div>
  );
}
