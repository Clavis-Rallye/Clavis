/**
 * The feedback bar: the tester's only guide during a test session. The kit is
 * the external tool that uploads the session plan and reads the events.
 *
 * - A browser takes part only after it opened a join link (`#qa-join=<key>`).
 *   Without a key the bar sends no request and renders nothing.
 * - Every event names the session it was made in. The server refuses an event
 *   of an ended session with 409.
 * - The App keeps the session plan. ok, skip, back and a block jump move the
 *   step on the server in the same request, and the answer carries the next
 *   card, so the tapping device shows it at once.
 * - The pill shows the assistant's message, the step, the role badge and the
 *   move buttons. The panel shows the card (needs, Do, Expect), the note
 *   field, point at, the blocks, dock and hide.
 * - Every event carries the action trail since the step began.
 * - Hidden means nothing in the DOM: not joined, before the first state, the
 *   kit's `hidden`, `?qa=off` or the hide button.
 * - The bar renders in a portal into its own container under `body`. That
 *   container stops `focusin` from bubbling to `document`, where MUI's modal
 *   focus trap would pull the focus back into an open dialog.
 * - Every control carries `data-testid="qa-bar-…"`, so the kit can tell the
 *   bar's clicks in the observer journal from the product's.
 */
import { useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import Box from '@mui/material/Box';
import ButtonBase from '@mui/material/ButtonBase';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import IconButton from '@mui/material/IconButton';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import AdsClickIcon from '@mui/icons-material/AdsClick';
import SkipPreviousIcon from '@mui/icons-material/SkipPrevious';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import EditNoteIcon from '@mui/icons-material/EditNote';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SendIcon from '@mui/icons-material/Send';
import SkipNextIcon from '@mui/icons-material/SkipNext';
import VerticalAlignBottomIcon from '@mui/icons-material/VerticalAlignBottom';
import VerticalAlignTopIcon from '@mui/icons-material/VerticalAlignTop';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { AuthContext } from '../auth/AuthContext';
import { BOTTOM_NAV_HEIGHT } from '../components/participantNav';
import {
  applyQaParam,
  buildContext,
  clearKey,
  fetchState,
  flushOutbox,
  newClientId,
  readDock,
  readKey,
  readLocalHidden,
  submitNote,
  readSession,
  takeJoinHash,
  writeDock,
  writeKey,
  writeLocalHidden,
  writeSession,
  type QaDock,
  type QaKind,
  type QaNoteInput,
  type QaState,
} from './qaApi';
import { resolveTarget, selectorOf, visibleText } from './pointer';
import { linkify } from './linkify';
import { isQaRole, pageDevice, pageRole, rolesMatch } from './roles';
import { TrailRecorder } from './trail';

export const POLL_MS = 3000;
export const BACKOFF_MS = 10000;
// After an own action, poll fast for a short while, so a move made on another
// device or by the kit shows within about a second.
export const FAST_POLL_MS = 1000;
export const FAST_WINDOW_MS = 10000;
const TOAST_MS = 2500;
// Above every app layer: MUI's modal is 1300, the snackbar 1400, the tooltip 1500.
const BAR_Z_INDEX = 2000;
// Below the participant app bar, so the top dock never covers its menu.
const TOP_OFFSET_PX = 72;
// The kit may send a card of numbered lines: keep its line breaks, still wrap long lines.
const CARD_TEXT_SX = { whiteSpace: 'pre-line' } as const;
// The bottom dock sits above the participant bottom nav.
export const BOTTOM_BASE_PX = BOTTOM_NAV_HEIGHT + 8;
// Every control is a touch target of at least this size.
const TOUCH_PX = 44;
const MOVE_KINDS: readonly QaKind[] = ['back', 'ok', 'skip'];

interface Target {
  selector: string;
  text: string;
}

interface Toast {
  message: string;
  tone: 'success' | 'info' | 'error';
}

function sameState(a: QaState | null, b: QaState): boolean {
  return a !== null && JSON.stringify(a) === JSON.stringify(b);
}

/** Keep the session the server names next to the key. */
function rememberSession(state: QaState): void {
  if (state.session_id && state.session_id !== readSession()) writeSession(state.session_id);
}

/** Whether `next` was written before `last`. Unknown times never count as older. */
function olderThan(next: string | null, last: string | null): boolean {
  if (!next || !last) return false;
  const a = Date.parse(next);
  const b = Date.parse(last);
  return Number.isFinite(a) && Number.isFinite(b) && a < b;
}

/**
 * The session state, polled every three seconds while the tab is visible and
 * a key is held. `boost` polls every second for the next ten seconds. `apply`
 * takes the state a POST answered with. A poll that says the browser is no
 * longer joined calls `onUnjoined` and stops.
 *
 * Ordering: a poll that started before a POST may answer after it, with
 * the card the POST just left. A generation counter guards against that. A
 * POST answer, a new key and a new session each start a new generation, and a
 * poll counts only if the generation it started in is still current. On top,
 * no state replaces one of the same session that the server wrote later.
 */
function useQaState(key: string | null, onUnjoined: () => void) {
  const [state, setState] = useState<QaState | null>(null);
  const boostRef = useRef<() => void>(() => undefined);
  const onUnjoinedRef = useRef(onUnjoined);
  const generationRef = useRef(0);
  const lastRef = useRef<QaState | null>(null);

  useEffect(() => {
    onUnjoinedRef.current = onUnjoined;
  });

  /** Show `next` unless it is stale. `startedIn` is the generation a poll began in, null for a POST answer. */
  const accept = useCallback((next: QaState, startedIn: number | null): boolean => {
    if (startedIn !== null && startedIn !== generationRef.current) return false;
    const last = lastRef.current;
    if (last && last.session_id === next.session_id && olderThan(next.updated_at, last.updated_at)) return false;
    if (last && last.session_id !== next.session_id) generationRef.current += 1;
    lastRef.current = next;
    rememberSession(next);
    setState((prev) => (sameState(prev, next) ? prev : next));
    return true;
  }, []);

  useEffect(() => {
    if (!key) return;
    const activeKey = key;
    // A new key is a new session as far as this browser knows.
    generationRef.current += 1;
    lastRef.current = null;
    let cancelled = false;
    let inFlight = false;
    let fastUntil = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = (ms: number) => {
      clearTimeout(timer);
      timer = setTimeout(() => void tick(), ms);
    };

    async function tick(): Promise<void> {
      if (cancelled || inFlight) return;
      // A hidden tab stops polling. `visibilitychange` starts it again.
      if (document.visibilityState === 'hidden') return;
      inFlight = true;
      let delay = Date.now() < fastUntil ? FAST_POLL_MS : POLL_MS;
      const startedIn = generationRef.current;
      try {
        const next = await fetchState(activeKey);
        if (cancelled) return;
        if (next === null) {
          cancelled = true;
          onUnjoinedRef.current();
          return;
        }
        accept(next, startedIn);
      } catch {
        delay = BACKOFF_MS;
      } finally {
        inFlight = false;
      }
      void flushOutbox(activeKey).catch(() => undefined);
      if (!cancelled) schedule(delay);
    }

    const onVisibility = () => {
      if (document.visibilityState !== 'hidden') schedule(0);
    };
    const onOnline = () => {
      void flushOutbox(activeKey).catch(() => undefined);
    };

    boostRef.current = () => {
      fastUntil = Date.now() + FAST_WINDOW_MS;
      if (!inFlight) schedule(FAST_POLL_MS);
    };

    void tick();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('online', onOnline);
    return () => {
      cancelled = true;
      boostRef.current = () => undefined;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('online', onOnline);
    };
  }, [key, accept]);

  const boost = useCallback(() => boostRef.current(), []);
  const apply = useCallback(
    (next: QaState) => {
      generationRef.current += 1;
      accept(next, null);
    },
    [accept],
  );
  return { state: key ? state : null, boost, apply };
}

/**
 * The key from a join link's fragment, stored at once. A new key forgets the
 * old session until the server names the new one. A join link also undoes a
 * local hide.
 */
function joinFrom(hash: string): string | null {
  const joined = takeJoinHash(hash).key;
  if (joined) {
    if (joined !== readKey()) writeSession(null);
    writeKey(joined);
    writeLocalHidden(false);
  }
  return joined;
}

export default function QaFeedbackBar() {
  const { t } = useTranslation('qa');
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useContext(AuthContext);

  // `#qa-join=`, `?qa=off` and `?qa=on` count on the first render and on every
  // route change. Reading them during render keeps the bar from flashing once.
  const [key, setKey] = useState<string | null>(() => joinFrom(location.hash) ?? readKey());
  const [localHidden, setLocalHidden] = useState(() => applyQaParam(location.search) ?? readLocalHidden());
  const [seenHash, setSeenHash] = useState(location.hash);
  const [seenSearch, setSeenSearch] = useState(location.search);
  if (seenHash !== location.hash) {
    setSeenHash(location.hash);
    const joined = joinFrom(location.hash);
    if (joined !== null) {
      if (joined !== key) setKey(joined);
      if (localHidden) setLocalHidden(false);
    }
  }
  if (seenSearch !== location.search) {
    setSeenSearch(location.search);
    const fromParam = applyQaParam(location.search);
    if (fromParam !== null && fromParam !== localHidden) setLocalHidden(fromParam);
  }

  // The key never stays in the address bar, the history or a shared link.
  useEffect(() => {
    const { key: joined, rest } = takeJoinHash(location.hash);
    if (joined === null) return;
    navigate({ pathname: location.pathname, search: location.search, hash: rest }, { replace: true });
  }, [location.pathname, location.search, location.hash, navigate]);

  const dropKey = useCallback(() => {
    clearKey();
    setKey(null);
  }, []);
  const { state, boost, apply } = useQaState(key, dropKey);

  const [expanded, setExpanded] = useState(false);
  const [text, setText] = useState('');
  const [target, setTarget] = useState<Target | null>(null);
  const [pointing, setPointing] = useState(false);
  const [dock, setDock] = useState<QaDock>(() => readDock());
  const [pending, setPending] = useState<QaKind | null>(null);
  const [pendingTarget, setPendingTarget] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  // The bottom offset that keeps the bar above an open phone keyboard, null without one.
  const [keyboardBottom, setKeyboardBottom] = useState<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const [container] = useState(() => {
    const element = document.createElement('div');
    element.setAttribute('data-qa-bar-root', '');
    return element;
  });
  const [recorder] = useState(() => new TrailRecorder());

  const joined = state !== null;
  const visible = joined && !state.hidden && !localHidden;
  const current = state?.current ?? null;

  // The container is in the DOM only while the bar is visible.
  useEffect(() => {
    if (!visible) return;
    const stopFocusIn = (event: Event) => event.stopPropagation();
    container.addEventListener('focusin', stopFocusIn);
    document.body.appendChild(container);
    return () => {
      container.removeEventListener('focusin', stopFocusIn);
      container.remove();
    };
  }, [visible, container]);

  // Above the phone keyboard: with the bottom dock, follow the visual viewport.
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!visible || dock !== 'bottom' || !viewport) return;
    const follow = () => {
      const covered = window.innerHeight - viewport.height - viewport.offsetTop + 8;
      setKeyboardBottom(covered > BOTTOM_BASE_PX ? covered : null);
    };
    follow();
    viewport.addEventListener('resize', follow);
    viewport.addEventListener('scroll', follow);
    return () => {
      viewport.removeEventListener('resize', follow);
      viewport.removeEventListener('scroll', follow);
      setKeyboardBottom(null);
    };
  }, [visible, dock]);

  // The trail runs while the browser is joined, also while the bar is hidden.
  useEffect(() => {
    if (!joined) return;
    return recorder.start(container);
  }, [joined, recorder, container]);

  useEffect(() => {
    recorder.clear();
  }, [current, recorder]);

  useEffect(() => {
    recorder.route(location.pathname, location.search);
  }, [location.pathname, location.search, recorder]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  const focusNote = useCallback(() => {
    setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

  // Point at: the next click outside the bar is swallowed and names the element.
  useEffect(() => {
    if (!pointing || !visible) return;
    const inBar = (event: Event) => event.target instanceof Node && container.contains(event.target);
    const canHover = typeof window.matchMedia === 'function' && window.matchMedia('(hover: hover)').matches;

    const swallow = (event: Event) => {
      if (inBar(event)) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    };
    const onClick = (event: MouseEvent) => {
      if (inBar(event)) return;
      swallow(event);
      const element = resolveTarget(event.target);
      setTarget({ selector: selectorOf(element), text: visibleText(element) });
      setPointing(false);
      setExpanded(true);
      focusNote();
    };
    // No preventDefault on touch: the page must still scroll while pointing.
    const onTouch = (event: TouchEvent) => {
      if (!inBar(event)) event.stopPropagation();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setPointing(false);
    };
    const onMove = (event: PointerEvent) => {
      const overlay = overlayRef.current;
      if (!overlay || !canHover || event.pointerType !== 'mouse') return;
      const element = inBar(event) ? null : resolveTarget(event.target);
      if (!element) {
        overlay.style.display = 'none';
        return;
      }
      const rect = element.getBoundingClientRect();
      Object.assign(overlay.style, {
        display: 'block',
        top: `${rect.top}px`,
        left: `${rect.left}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
      });
    };

    document.addEventListener('pointerdown', swallow, true);
    document.addEventListener('mousedown', swallow, true);
    document.addEventListener('mouseup', swallow, true);
    document.addEventListener('click', onClick, true);
    document.addEventListener('touchstart', onTouch, { capture: true, passive: true });
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointermove', onMove, { capture: true, passive: true });
    return () => {
      document.removeEventListener('pointerdown', swallow, true);
      document.removeEventListener('mousedown', swallow, true);
      document.removeEventListener('mouseup', swallow, true);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('touchstart', onTouch, { capture: true });
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointermove', onMove, { capture: true });
    };
  }, [pointing, visible, container, focusNote]);

  const user = auth?.user ?? null;
  const actualRole = pageRole(user, location.pathname);
  const contextRole = actualRole === 'signed_out' ? null : actualRole;
  const org = user?.organization_slug ?? null;

  const send = async (kind: QaKind, goTarget: string | null = null) => {
    if (!key || !state) return;
    const trimmed = text.trim();
    if (kind === 'note' && !trimmed) return;
    // ok, back and goto only report a step move; skip and note carry the field and the element.
    const withDetail = kind === 'skip' || kind === 'note';
    const note: QaNoteInput = {
      client_id: newClientId(),
      session_id: state.session_id ?? readSession(),
      kind,
      step: state.current,
      target: kind === 'goto' ? goTarget : null,
      text: withDetail ? trimmed || null : null,
      // `clampNote` redacts it before it leaves the browser.
      route: location.pathname + location.search,
      selector: withDetail ? (target?.selector ?? null) : null,
      element_text: withDetail ? (target?.text ?? null) : null,
      answers_message: kind === 'note' ? state.message : null,
      context: buildContext(contextRole, org),
      trail: recorder.snapshot(),
    };
    setPending(kind);
    setPendingTarget(note.target);
    const result = await submitNote(note, key);
    setPending(null);
    setPendingTarget(null);
    if (result.status === 'unjoined') {
      dropKey();
      return;
    }
    if (result.status === 'failed') {
      setToast({ message: t('toast.failed', { status: result.httpStatus }), tone: 'error' });
      return;
    }
    if (result.status === 'ended') {
      setToast({ message: t('toast.ended'), tone: 'error' });
      boost();
      return;
    }
    boost();
    if (withDetail) {
      setText('');
      setTarget(null);
    }
    if (result.status === 'queued') {
      setToast({ message: t('toast.queued'), tone: 'info' });
      return;
    }
    const answer = result.note;
    const after = answer.state ?? state;
    if (answer.state) apply(answer.state);
    if (!answer.moved && (kind === 'ok' || kind === 'skip') && after.count > 0 && after.index >= after.count - 1) {
      setToast({ message: t('toast.end'), tone: 'info' });
    } else if (!answer.moved && kind === 'back' && after.index === 0) {
      setToast({ message: t('toast.start'), tone: 'info' });
    } else if (kind === 'goto') {
      setToast({ message: t('toast.goto', { step: after.current ?? goTarget ?? '' }), tone: 'success' });
    } else {
      setToast({ message: t(`toast.${kind}`), tone: 'success' });
    }
  };

  if (!visible) return null;

  const card = state.card;
  const roleLabel = (role: string) => (isQaRole(role) ? t(`role.names.${role}`) : role);
  const expectedRole = card?.role || null;
  const roleMatches = expectedRole ? rolesMatch(expectedRole, actualRole) : true;
  const expectedDevice = card?.device === 'phone' || card?.device === 'desktop' ? card.device : null;
  const wrongDevice = expectedDevice !== null && expectedDevice !== pageDevice();

  const toggleDock = () => {
    const next: QaDock = dock === 'bottom' ? 'top' : 'bottom';
    writeDock(next);
    setDock(next);
  };

  const hide = () => {
    writeLocalHidden(true);
    setLocalHidden(true);
  };

  const togglePointing = () => {
    if (overlayRef.current) overlayRef.current.style.display = 'none';
    setPointing((value) => !value);
  };

  const answerMessage = () => {
    setExpanded(true);
    focusNote();
  };

  const dockOffset =
    dock === 'bottom'
      ? {
          bottom:
            keyboardBottom !== null
              ? `${keyboardBottom}px`
              : `calc(${BOTTOM_BASE_PX}px + env(safe-area-inset-bottom, 0px))`,
        }
      : { top: `${TOP_OFFSET_PX}px` };

  const toastColor =
    toast?.tone === 'error' ? 'error.main' : toast?.tone === 'info' ? 'info.main' : 'success.main';

  const cardSection = (testId: string, label: string, value: string | null) =>
    value ? (
      <Typography variant="body2" sx={{ mt: 0.5, ...CARD_TEXT_SX }} data-testid={testId}>
        <Box component="span" sx={{ fontWeight: 600 }}>
          {label}:
        </Box>{' '}
        {linkify(value)}
      </Typography>
    ) : null;

  const panel = (
    <Paper
      elevation={8}
      data-testid="qa-bar-panel"
      sx={{ p: 1.5, width: '100%', minWidth: 0, pointerEvents: 'auto', boxSizing: 'border-box' }}
    >
      <Stack spacing={1}>
        <Box sx={{ maxHeight: '40vh', overflowY: 'auto' }}>
          <Typography variant="subtitle2" component="p" sx={{ fontWeight: 600, ...CARD_TEXT_SX }}>
            <Box component="span" sx={{ fontFamily: 'monospace', mr: 1 }}>
              {card?.id ?? t('step.none')}
            </Box>
            {linkify(card?.title)}
          </Typography>
          {cardSection('qa-bar-needs', t('step.needs'), card?.needs ?? null)}
          {cardSection('qa-bar-do', t('step.do'), card?.do ?? null)}
          {cardSection('qa-bar-expect', t('step.expect'), card?.expect ?? null)}
        </Box>
        <TextField
          multiline
          minRows={2}
          maxRows={6}
          size="small"
          label={state.message ? t('field.answerLabel') : t('field.label')}
          placeholder={t('field.placeholder')}
          helperText={t('field.help')}
          value={text}
          onChange={(event) => setText(event.target.value)}
          inputRef={inputRef}
          slotProps={{ htmlInput: { 'data-testid': 'qa-bar-text', maxLength: 4000 } }}
        />
        {target && (
          <Box
            data-testid="qa-bar-target"
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              minWidth: 0,
              pl: 1,
              borderRadius: 1,
              bgcolor: 'action.selected',
            }}
          >
            <Typography variant="body2" sx={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
              {target.text ? `${target.selector} · ${target.text}` : target.selector}
            </Typography>
            <IconButton
              aria-label={t('actions.removeTarget')}
              title={t('actions.removeTarget')}
              onClick={() => setTarget(null)}
              data-testid="qa-bar-target-remove"
              sx={{ width: TOUCH_PX, height: TOUCH_PX, flexShrink: 0 }}
            >
              <CloseIcon />
            </IconButton>
          </Box>
        )}
        <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
          <Button
            size="small"
            variant={pointing ? 'contained' : 'outlined'}
            color={pointing ? 'warning' : 'primary'}
            startIcon={<AdsClickIcon />}
            onClick={togglePointing}
            aria-pressed={pointing}
            data-testid="qa-bar-point"
            sx={{ minHeight: TOUCH_PX }}
          >
            {pointing ? t('actions.pointCancel') : t('actions.point')}
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={pending === 'note' ? <CircularProgress size={16} color="inherit" /> : <SendIcon />}
            disabled={pending !== null || !text.trim()}
            onClick={() => void send('note')}
            data-testid="qa-bar-send"
            sx={{ minHeight: TOUCH_PX }}
          >
            {t('actions.send')}
          </Button>
        </Stack>
        {state.blocks.length > 0 && (
          <Box>
            <Typography variant="caption" component="p" sx={{ fontWeight: 600, mb: 0.5 }}>
              {t('blocks.label')}
            </Typography>
            <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5 }} data-testid="qa-bar-blocks">
              {state.blocks.map((block) => {
                const isCurrent = card?.block === block.id;
                return (
                  <Button
                    key={block.id}
                    size="small"
                    variant={isCurrent ? 'contained' : 'outlined'}
                    disabled={pending !== null}
                    aria-current={isCurrent ? 'step' : undefined}
                    onClick={() => void send('goto', block.first)}
                    data-testid={`qa-bar-block-${block.id}`}
                    sx={{ textTransform: 'none', minHeight: TOUCH_PX, maxWidth: '100%', overflowWrap: 'anywhere' }}
                  >
                    {pending === 'goto' && pendingTarget === block.first ? (
                      <CircularProgress size={14} sx={{ mr: 0.5 }} data-testid="qa-bar-spinner" />
                    ) : null}
                    {block.title ? `${block.id} · ${block.title}` : block.id}
                  </Button>
                );
              })}
            </Stack>
          </Box>
        )}
        <Stack direction="row" spacing={1} sx={{ justifyContent: 'space-between' }}>
          <Button
            size="small"
            startIcon={dock === 'bottom' ? <VerticalAlignTopIcon /> : <VerticalAlignBottomIcon />}
            onClick={toggleDock}
            data-testid="qa-bar-dock"
            sx={{ minHeight: TOUCH_PX }}
          >
            {dock === 'bottom' ? t('actions.dockTop') : t('actions.dockBottom')}
          </Button>
          <Button
            size="small"
            color="inherit"
            startIcon={<VisibilityOffIcon />}
            onClick={hide}
            title={t('actions.hideHint')}
            data-testid="qa-bar-hide"
            sx={{ minHeight: TOUCH_PX }}
          >
            {t('actions.hide')}
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );

  // The pill is the tester's main reading surface: the assistant's message, the step id
  // and up to two lines of the title in body size, the role badge, and every control a
  // 44 px touch target.
  const touch = { width: TOUCH_PX, height: TOUCH_PX } as const;
  const moveButton = (kind: QaKind, label: string, icon: ReactNode, color?: 'success') => (
    <IconButton
      color={color}
      aria-label={label}
      title={label}
      disabled={pending !== null || !current}
      aria-busy={pending === kind}
      onClick={() => void send(kind)}
      data-testid={`qa-bar-${kind}`}
      sx={touch}
    >
      {pending === kind ? <CircularProgress size={22} data-testid="qa-bar-spinner" /> : icon}
    </IconButton>
  );

  const pill = (
    <Paper
      elevation={8}
      data-testid="qa-bar-pill"
      sx={{ width: '100%', minWidth: 0, boxSizing: 'border-box', borderRadius: 2, p: 1, pointerEvents: 'auto' }}
    >
      {state.message && (
        <Box
          data-testid="qa-bar-message"
          sx={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 1,
            mb: 0.75,
            p: 1,
            borderRadius: 1,
            bgcolor: 'warning.light',
            color: 'warning.contrastText',
          }}
        >
          <Typography
            component="p"
            sx={{
              flex: 1,
              minWidth: 0,
              fontSize: '0.95rem',
              lineHeight: 1.35,
              display: '-webkit-box',
              WebkitLineClamp: 3,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              overflowWrap: 'anywhere',
              ...CARD_TEXT_SX,
            }}
          >
            <Box component="span" sx={{ fontWeight: 700 }}>
              {t('message.label')}:
            </Box>{' '}
            {state.message}
          </Typography>
          <Button
            size="small"
            variant="contained"
            color="inherit"
            onClick={answerMessage}
            data-testid="qa-bar-answer"
            sx={{ minHeight: TOUCH_PX, flexShrink: 0 }}
          >
            {t('actions.answer')}
          </Button>
        </Box>
      )}
      <ButtonBase
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-label={expanded ? t('actions.collapse') : t('actions.expand')}
        data-testid="qa-bar-headline"
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'flex-start',
          gap: 1,
          width: '100%',
          textAlign: 'left',
          borderRadius: 1,
          px: 0.5,
          py: 0.25,
        }}
      >
        <Box component="span" sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
          <Typography
            component="span"
            data-testid="qa-bar-step"
            sx={{ fontFamily: 'monospace', fontWeight: 700, fontSize: '1rem', lineHeight: 1.35, whiteSpace: 'nowrap' }}
          >
            {card?.id ?? '–'}
          </Typography>
          {state.count > 0 && (
            <Typography
              component="span"
              data-testid="qa-bar-position"
              sx={{ fontSize: '0.75rem', color: 'text.secondary', whiteSpace: 'nowrap' }}
            >
              {state.index + 1} / {state.count}
            </Typography>
          )}
        </Box>
        <Typography
          component="span"
          data-testid={pointing ? 'qa-bar-pointing' : 'qa-bar-title'}
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize: '1rem',
            lineHeight: 1.35,
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            overflowWrap: 'anywhere',
            color: pointing ? 'warning.dark' : 'text.primary',
          }}
        >
          {pointing ? t('pointing.hint') : (card?.title ?? '')}
        </Typography>
      </ButtonBase>
      {expectedRole && (
        <Typography
          component="p"
          data-testid="qa-bar-role"
          data-match={roleMatches ? 'yes' : 'no'}
          sx={{
            mt: 0.5,
            px: 0.75,
            py: 0.25,
            borderRadius: 1,
            fontSize: '0.85rem',
            fontWeight: 600,
            bgcolor: roleMatches ? 'success.light' : 'warning.light',
            color: roleMatches ? 'success.contrastText' : 'warning.contrastText',
          }}
        >
          {roleMatches
            ? t('role.youAre', { role: roleLabel(actualRole) })
            : t('role.mismatch', { needs: roleLabel(expectedRole), you: roleLabel(actualRole) })}
        </Typography>
      )}
      {wrongDevice && (
        <Typography
          component="p"
          data-testid="qa-bar-device"
          sx={{
            mt: 0.5,
            px: 0.75,
            py: 0.25,
            borderRadius: 1,
            fontSize: '0.85rem',
            fontWeight: 600,
            bgcolor: 'warning.light',
            color: 'warning.contrastText',
          }}
        >
          {t(`device.${expectedDevice}`)}
        </Typography>
      )}
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mt: 0.5 }}>
        {MOVE_KINDS.map((kind) =>
          moveButton(
            kind,
            t(`actions.${kind}`),
            kind === 'back' ? <SkipPreviousIcon /> : kind === 'ok' ? <CheckIcon /> : <SkipNextIcon />,
            kind === 'ok' ? 'success' : undefined,
          ),
        )}
        <IconButton
          color="primary"
          aria-label={t('actions.note')}
          title={t('actions.note')}
          onClick={() => {
            setExpanded(true);
            focusNote();
          }}
          data-testid="qa-bar-note"
          sx={touch}
        >
          <EditNoteIcon />
        </IconButton>
        <IconButton
          aria-label={expanded ? t('actions.collapse') : t('actions.expand')}
          title={expanded ? t('actions.collapse') : t('actions.expand')}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          data-testid="qa-bar-expand"
          sx={touch}
        >
          {(dock === 'bottom') === expanded ? <ExpandMoreIcon /> : <ExpandLessIcon />}
        </IconButton>
      </Stack>
    </Paper>
  );

  return createPortal(
    <>
      <Box
        ref={overlayRef}
        aria-hidden
        sx={{
          display: 'none',
          position: 'fixed',
          zIndex: BAR_Z_INDEX - 1,
          pointerEvents: 'none',
          outline: '2px solid',
          outlineColor: 'warning.main',
          backgroundColor: 'rgba(255, 167, 38, 0.12)',
        }}
      />
      <Box
        data-testid="qa-bar"
        role="region"
        aria-label={t('bar.label')}
        sx={{
          position: 'fixed',
          right: 8,
          zIndex: BAR_Z_INDEX,
          // Never wider than the screen: no horizontal scroll on a phone.
          width: 'min(420px, calc(100vw - 16px))',
          maxWidth: 'calc(100vw - 16px)',
          minWidth: 0,
          boxSizing: 'border-box',
          overflowWrap: 'anywhere',
          display: 'flex',
          flexDirection: dock === 'bottom' ? 'column' : 'column-reverse',
          alignItems: 'flex-end',
          gap: 1,
          pointerEvents: 'none',
          ...dockOffset,
        }}
      >
        <Box
          role="status"
          aria-live="polite"
          data-testid="qa-bar-toast"
          sx={{
            pointerEvents: 'none',
            ...(toast && {
              px: 1.5,
              py: 0.75,
              borderRadius: 1,
              color: 'common.white',
              backgroundColor: toastColor,
              boxShadow: 4,
              typography: 'body2',
            }),
          }}
        >
          {toast?.message ?? ''}
        </Box>
        {expanded && panel}
        {pill}
      </Box>
    </>,
    container,
  );
}
