'use client';

/**
 * The user study (docs/user-study): one participant per session, run by the
 * facilitator in their own signed-in browser.
 *
 * Within subjects: every participant does three tasks with VisioVox and three
 * with a baseline - the same video with its original audio and nothing else -
 * on two different recordings, so the second block cannot be answered from
 * memory of the first. Which recording goes with which condition, and which
 * condition comes first, rotate over a 4-way Latin square by participant
 * number. Each task is timed from Start to Done; answers are scored later
 * against the facilitator's key. The participant is an anonymous code.
 */

import { useEffect, useMemo, useState } from 'react';
import type { ProjectResponse } from '@visiovox/ts-client';

import { InsightsPanel } from '@/components/InsightsPanel';
import { Player } from '@/components/Player';
import { AppPage, AppShell, PageHeader } from '@/components/shell/AppShell';
import { TranscriptPanel } from '@/components/TranscriptPanel';
import { Alert, Button, Checkbox, Field, Select, Slab, SlabHead, TextArea } from '@/components/ui';
import type { Manifest } from '@/lib/playback/manifest';
import { api, useRequireSignIn } from '@/lib/store';
import {
  arrangement,
  participantNumber,
  susScore,
  type Condition,
  type RecordingKey,
} from '@/lib/study';

interface TaskSpec {
  id: string;
  question: string;
  key: string;
}

interface Setup {
  projects: Record<RecordingKey, string>;
  tasks: Record<RecordingKey, TaskSpec[]>;
}

const DEFAULT_TASKS: TaskSpec[] = [
  {
    id: 'T1',
    question:
      'While two people talk at the same time (around 0:__), what exactly did ______ say? Write their words as closely as you can.',
    key: '',
  },
  {
    id: 'T2',
    question: 'Find the moment where someone mentions "______". Write the time (mm:ss).',
    key: '',
  },
  {
    id: 'T3',
    question: 'What did ______ say they would do, and who will do it?',
    key: '',
  },
];

/** The ten System Usability Scale statements (Brooke, 1996), "the system" = VisioVox. */
const SUS = [
  'I think that I would like to use this system frequently.',
  'I found the system unnecessarily complex.',
  'I thought the system was easy to use.',
  'I think that I would need the support of a technical person to be able to use this system.',
  'I found the various functions in this system were well integrated.',
  'I thought there was too much inconsistency in this system.',
  'I would imagine that most people would learn to use this system very quickly.',
  'I found the system very cumbersome to use.',
  'I felt very confident using the system.',
  'I needed to learn a lot of things before I could get going with this system.',
];

const STORAGE = 'visiovox.study.setup';

interface TaskResult {
  task: string;
  condition: Condition;
  recording: RecordingKey;
  seconds: number;
  answer: string;
}

type Stage =
  | { kind: 'setup' }
  | { kind: 'block'; block: number; task: number; startedAt: number | null }
  | { kind: 'questionnaire' }
  | { kind: 'done' };

export default function StudyPage() {
  const authed = useRequireSignIn();
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [setup, setSetup] = useState<Setup>({
    projects: { A: '', B: '' },
    tasks: { A: DEFAULT_TASKS, B: DEFAULT_TASKS },
  });
  const [participant, setParticipant] = useState('P01');
  const [consented, setConsented] = useState(false);
  const [stage, setStage] = useState<Stage>({ kind: 'setup' });
  const [manifests, setManifests] = useState<Partial<Record<RecordingKey, Manifest>>>({});
  const [answer, setAnswer] = useState('');
  const [results, setResults] = useState<TaskResult[]>([]);
  const [sus, setSus] = useState<number[]>(Array.from({ length: 10 }, () => 0));
  const [preference, setPreference] = useState<'visiovox' | 'baseline' | 'no-preference' | ''>('');
  const [comments, setComments] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<number | null>(null);

  // The facilitator's setup survives a reload of this browser only.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE);
      if (raw !== null) setSetup(JSON.parse(raw) as Setup);
    } catch {
      /* no saved setup */
    }
  }, []);
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE, JSON.stringify(setup));
    } catch {
      /* storage unavailable */
    }
  }, [setup]);

  useEffect(() => {
    if (!authed) return;
    void api()
      .listProjects(100)
      .then((r) => {
        setProjects(r.items.filter((p) => p.status === 'ready'));
      });
    void api()
      .listStudySessions()
      .then((s) => {
        setSessions(s.length);
      })
      .catch(() => undefined);
  }, [authed]);

  const plan = useMemo(() => arrangement(participantNumber(participant)), [participant]);

  const begin = async () => {
    setError(null);
    try {
      const [a, b] = await Promise.all([
        api().getManifest(setup.projects.A),
        api().getManifest(setup.projects.B),
      ]);
      setManifests({ A: a as Manifest, B: b as Manifest });
      setResults([]);
      setAnswer('');
      setStage({ kind: 'block', block: 0, task: 0, startedAt: null });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The recordings could not be loaded.');
    }
  };

  const save = async () => {
    setError(null);
    try {
      await api().saveStudySession({
        participant,
        order: plan[0].condition === 'visiovox' ? 'visiovox-first' : 'baseline-first',
        tasks: results,
        sus,
        preference: preference === '' ? 'no-preference' : preference,
        comments,
      });
      setSessions((n) => (n ?? 0) + 1);
      setStage({ kind: 'done' });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The session could not be saved.');
    }
  };

  const exportCsv = async () => {
    const rows = await api().listStudySessions();
    const keys = new Map<string, string>();
    for (const rec of ['A', 'B'] as const)
      for (const t of setup.tasks[rec]) keys.set(`${rec}:${t.id}`, t.key);
    const quote = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
    const header = [
      'participant', 'order', 'task', 'condition', 'recording', 'seconds', 'answer',
      'answer_key', 'correct', 'sus_score', 'preference', 'comments',
    ]; // prettier-ignore
    const lines = [header.join(',')];
    for (const s of rows) {
      const data = s.data as {
        order: string;
        tasks: TaskResult[];
        sus: number[];
        preference: string;
        comments: string;
      };
      for (const t of data.tasks) {
        lines.push(
          [
            s.participant,
            data.order,
            t.task,
            t.condition,
            t.recording,
            t.seconds.toFixed(1),
            t.answer,
            keys.get(`${t.recording}:${t.task}`) ?? '',
            '', // scored by hand: 1 correct, 0 not
            susScore(data.sus).toFixed(1),
            data.preference,
            data.comments,
          ]
            .map(quote)
            .join(','),
        );
      }
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'visiovox-user-study.csv';
    a.click();
  };

  // ------------------------------------------------------------------ views

  if (stage.kind === 'block') {
    const step = plan[stage.block];
    if (step === undefined) return null;
    const manifest = manifests[step.recording];
    const tasks = setup.tasks[step.recording];
    const task = tasks[stage.task];
    if (manifest === undefined || task === undefined) return null;
    // The baseline: the same picture with its original sound - no speakers,
    // captions, transcript or insights.
    const shown: Manifest =
      step.condition === 'baseline' ? { ...manifest, speakers: [] } : manifest;
    const running = stage.startedAt !== null;
    return (
      <AppShell>
        <AppPage>
          <PageHeader
            title={`Part ${String(stage.block + 1)} of 2 · task ${String(stage.task + 1)} of ${String(tasks.length)}`}
            description={
              step.condition === 'visiovox'
                ? 'Use VisioVox: pick a person, read the transcript, click a face.'
                : 'Use the video player as you normally would.'
            }
          />
          <Slab className="flex flex-col gap-4 p-5">
            <p className="text-[16px] font-medium leading-relaxed">{task.question}</p>
            {!running ? (
              <Button
                onClick={() => {
                  setAnswer('');
                  setStage({ ...stage, startedAt: performance.now() });
                }}
              >
                Start task
              </Button>
            ) : (
              <>
                <TextArea
                  label="Your answer"
                  value={answer}
                  onChange={(e) => {
                    setAnswer(e.target.value);
                  }}
                  rows={3}
                />
                <Button
                  onClick={() => {
                    const seconds = (performance.now() - (stage.startedAt ?? 0)) / 1000;
                    setResults((r) => [
                      ...r,
                      {
                        task: task.id,
                        condition: step.condition,
                        recording: step.recording,
                        seconds: Math.round(seconds * 10) / 10,
                        answer,
                      },
                    ]);
                    if (stage.task + 1 < tasks.length)
                      setStage({
                        kind: 'block',
                        block: stage.block,
                        task: stage.task + 1,
                        startedAt: null,
                      });
                    else if (stage.block === 0)
                      setStage({ kind: 'block', block: 1, task: 0, startedAt: null });
                    else setStage({ kind: 'questionnaire' });
                  }}
                >
                  Done
                </Button>
              </>
            )}
          </Slab>
          {running && (
            <Slab className="p-4 sm:p-6">
              <Player
                key={`${step.condition}-${step.recording}`}
                manifest={shown}
                {...(step.condition === 'visiovox'
                  ? {
                      aside: (context) => <TranscriptPanel context={context} title="Recording" />,
                      below: (context) => (
                        <InsightsPanel context={context} durationMs={manifest.duration_ms} />
                      ),
                    }
                  : {})}
              />
            </Slab>
          )}
        </AppPage>
      </AppShell>
    );
  }

  if (stage.kind === 'questionnaire') {
    return (
      <AppShell>
        <AppPage narrow>
          <PageHeader
            title="About VisioVox"
            description="For each statement, how much do you agree? 1 = strongly disagree, 5 = strongly agree."
          />
          <Slab className="flex flex-col gap-4 p-5">
            {SUS.map((statement, i) => (
              <fieldset key={statement} className="flex flex-col gap-2">
                <legend className="text-[14px]">
                  {String(i + 1)}. {statement}
                </legend>
                <div className="flex gap-2">
                  {[1, 2, 3, 4, 5].map((v) => (
                    <label
                      key={v}
                      className={
                        'grid size-10 cursor-pointer place-items-center rounded-[var(--radius-sm)] border text-[14px] font-medium ' +
                        (sus[i] === v
                          ? 'border-accent bg-accent-soft text-accent'
                          : 'border-border-default')
                      }
                    >
                      <input
                        type="radio"
                        name={`sus-${String(i)}`}
                        value={v}
                        className="sr-only"
                        checked={sus[i] === v}
                        onChange={() => {
                          setSus((s) => s.map((x, k) => (k === i ? v : x)));
                        }}
                      />
                      {v}
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
            <Select
              label="Which would you rather use for this kind of task?"
              value={preference}
              onChange={(e) => {
                setPreference(e.target.value as typeof preference);
              }}
            >
              <option value="">Choose…</option>
              <option value="visiovox">VisioVox</option>
              <option value="baseline">The normal video player</option>
              <option value="no-preference">No preference</option>
            </Select>
            <TextArea
              label="Anything else? (optional)"
              value={comments}
              onChange={(e) => {
                setComments(e.target.value);
              }}
              rows={3}
            />
            {error !== null && <Alert tone="danger">{error}</Alert>}
            <Button
              disabled={sus.some((v) => v === 0) || preference === ''}
              onClick={() => {
                void save();
              }}
            >
              Finish and save
            </Button>
          </Slab>
        </AppPage>
      </AppShell>
    );
  }

  if (stage.kind === 'done') {
    return (
      <AppShell>
        <AppPage narrow>
          <PageHeader title="Saved - thank you" description={`Session ${participant} is stored.`} />
          <Button
            onClick={() => {
              const next = participantNumber(participant) + 1;
              setParticipant(`P${String(next).padStart(2, '0')}`);
              setConsented(false);
              setSus(Array.from({ length: 10 }, () => 0));
              setPreference('');
              setComments('');
              setStage({ kind: 'setup' });
            }}
          >
            Next participant
          </Button>
        </AppPage>
      </AppShell>
    );
  }

  // setup
  const ready =
    setup.projects.A !== '' &&
    setup.projects.B !== '' &&
    setup.projects.A !== setup.projects.B &&
    consented;
  return (
    <AppShell>
      <AppPage>
        <PageHeader
          title="User study"
          description="Facilitator setup. Run one participant at a time; see docs/user-study for the protocol."
        />
        <div className="grid items-start gap-6 lg:grid-cols-2">
          {(['A', 'B'] as const).map((rec) => (
            <Slab key={rec}>
              <SlabHead label={`Recording ${rec}`} description="Its three tasks and answer key" />
              <div className="flex flex-col gap-4 p-5">
                <Select
                  label="Recording"
                  value={setup.projects[rec]}
                  onChange={(e) => {
                    setSetup({ ...setup, projects: { ...setup.projects, [rec]: e.target.value } });
                  }}
                >
                  <option value="">Choose a processed recording…</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title}
                    </option>
                  ))}
                </Select>
                {setup.tasks[rec].map((t, i) => (
                  <div
                    key={t.id}
                    className="flex flex-col gap-2 rounded-[var(--radius-md)] bg-bg-subtle p-3"
                  >
                    <TextArea
                      label={`${t.id} - question`}
                      value={t.question}
                      rows={2}
                      onChange={(e) => {
                        const tasks = setup.tasks[rec].map((x, k) =>
                          k === i ? { ...x, question: e.target.value } : x,
                        );
                        setSetup({ ...setup, tasks: { ...setup.tasks, [rec]: tasks } });
                      }}
                    />
                    <Field
                      label="Answer key (never shown to the participant)"
                      value={t.key}
                      onChange={(e) => {
                        const tasks = setup.tasks[rec].map((x, k) =>
                          k === i ? { ...x, key: e.target.value } : x,
                        );
                        setSetup({ ...setup, tasks: { ...setup.tasks, [rec]: tasks } });
                      }}
                    />
                  </div>
                ))}
              </div>
            </Slab>
          ))}
        </div>
        <Slab className="flex flex-col gap-4 p-5">
          <Field
            label="Participant code (P01, P02, … - never a name)"
            value={participant}
            onChange={(e) => {
              setParticipant(e.target.value.trim());
            }}
          />
          <p className="text-[13.5px] text-fg-secondary">
            This participant does part 1 with{' '}
            <strong>{plan[0].condition === 'visiovox' ? 'VisioVox' : 'the baseline'}</strong> on
            recording {plan[0].recording}, then part 2 with{' '}
            <strong>{plan[1].condition === 'visiovox' ? 'VisioVox' : 'the baseline'}</strong> on
            recording {plan[1].recording}.
          </p>
          <Checkbox checked={consented} onChange={setConsented}>
            The participant has read the information sheet and signed the consent form.
          </Checkbox>
          {error !== null && <Alert tone="danger">{error}</Alert>}
          <div className="flex flex-wrap gap-3">
            <Button disabled={!ready} onClick={() => void begin()}>
              Start session
            </Button>
            <Button variant="secondary" onClick={() => void exportCsv()}>
              Export results (CSV){sessions !== null ? ` - ${String(sessions)} saved` : ''}
            </Button>
          </div>
        </Slab>
      </AppPage>
    </AppShell>
  );
}
