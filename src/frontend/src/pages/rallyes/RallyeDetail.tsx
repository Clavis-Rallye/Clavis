import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { excerpt } from '../../utils/richtext';
import { Link as RouterLink, useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { dirAttr, localizedText, resolveLocalized } from '../../i18n/contentLocales';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Collapse,
  Divider,
  FormControlLabel,
  IconButton,
  Link,
  Stack,
  Switch,
  Tooltip,
  Typography,
  Snackbar,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import FileCopyOutlinedIcon from '@mui/icons-material/FileCopyOutlined';
import LinkIcon from '@mui/icons-material/Link';
import DeleteIcon from '@mui/icons-material/Delete';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import EditIcon from '@mui/icons-material/Edit';
import EmojiEventsOutlinedIcon from '@mui/icons-material/EmojiEventsOutlined';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import PlaceIcon from '@mui/icons-material/Place';
import ScheduleIcon from '@mui/icons-material/Schedule';
import {
  DndContext,
  DragOverlay,
  type DragEndEvent,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import { restrictToVerticalAxis, restrictToParentElement } from '@dnd-kit/modifiers';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  copyRallyeStation,
  copyRallyeStationChallenge,
  deleteRallye,
  duplicateRallye,
  getRallye,
  importChallengeToRallyeStation,
  importStation,
  listRallyeStationChallenges,
  listRallyeStations,
  reorderRallyeStationChallenges,
  reorderStations,
  unlinkRallyeStationChallenge,
  unlinkStation,
  updateRallye,
} from '../../api/content';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type {
  MultilingualString,
  Rallye,
  RallyeStationChallengeLink,
  RallyeStationLink,
} from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import MultilingualField, { type MultilingualFieldHandle } from '../../components/MultilingualField';
import SafeHtml from '../../components/SafeHtml';
import BackLink from '../../components/BackLink';
import AddChoiceDialog from '../../components/AddChoiceDialog';
import ImportStationDialog from '../../components/ImportStationDialog';
import ImportChallengeDialog from '../../components/ImportChallengeDialog';
import { sortableA11y } from '../../components/sortableA11y';

type DialogKind = 'delete' | 'duplicate' | null;

// The task preview resolves in the current content language first
// (resolveLocalized already falls through de/en and empty strings), and when
// none of those has text, in the first stored language that does.
const resolveTaskText = (
  ml: MultilingualString,
  activeLang: string,
): { text: string; locale: string | null } => {
  const resolved = resolveLocalized(ml, activeLang);
  if (resolved.text.trim()) return resolved;
  const first = Object.entries(ml ?? {}).find(([, v]) => typeof v === 'string' && v.trim());
  return first ? { text: first[1], locale: first[0] } : resolved;
};

// --------------------------------------------------------------------------
// Challenge row (inside an expanded station): drag handle + remove.
// --------------------------------------------------------------------------
// The library stays shared, and the rallye editor shows it without explaining
// it. An item in more than one rallye carries a chip with the count, which
// opens its page with the usage list, and an action that gives this rallye its
// own copy.
const SharedActions: React.FC<{
  count: number;
  onOpen: () => void;
  onEditCopy: () => void;
}> = ({ count, onOpen, onEditCopy }) => {
  const { t } = useTranslation('content');
  if (count < 2) return null;
  return (
    <>
      <Tooltip title={t('composition.sharedTooltip', { count })}>
        <Chip
          size="small"
          variant="outlined"
          icon={<LinkIcon fontSize="small" />}
          label={t('composition.sharedIn', { count })}
          onClick={onOpen}
          sx={{ alignSelf: 'center' }}
        />
      </Tooltip>
      <Tooltip title={t('composition.editAsCopy')}>
        <IconButton aria-label={t('composition.editAsCopy')} onClick={onEditCopy}>
          <FileCopyOutlinedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </>
  );
};

interface SortableChallengeRowProps {
  link: RallyeStationChallengeLink;
  index: number;
  label: string;
  typeLabel: string;
  metrics?: string;
  taskText: string;
  taskDir?: 'rtl';
  taskLabel: string;
  taskToggleLabel: string;
  onRemove: () => void;
  onOpen: () => void;
  onEditCopy: () => void;
  dragHandleLabel: string;
  removeLabel: string;
  editLabel: string;
}

const SortableChallengeRow: React.FC<SortableChallengeRowProps> = ({
  link,
  index,
  label,
  typeLabel,
  metrics,
  taskText,
  taskDir,
  taskLabel,
  taskToggleLabel,
  onRemove,
  onOpen,
  onEditCopy,
  dragHandleLabel,
  removeLabel,
  editLabel,
}) => {
  const [taskOpen, setTaskOpen] = useState(false);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: link.challenge.id,
  });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };
  return (
    <Box
      ref={setNodeRef}
      style={style}
      aria-roledescription="sortable item"
      sx={{ borderTop: 1, borderColor: 'divider' }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          pl: 4,
          pr: 1,
          py: 1,
          '&:hover': { bgcolor: 'action.selected' },
        }}
      >
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="body2" noWrap>
            {`${index + 1}. ${label}`}
          </Typography>
          {metrics && (
            <Typography variant="caption" color="text.secondary">
              {metrics}
            </Typography>
          )}
        </Box>
        <Chip label={typeLabel} size="small" />
        <SharedActions count={link.challenge.usage_count ?? 0} onOpen={onOpen} onEditCopy={onEditCopy} />
        {/* The station row has this button too. Without it, one would have to
            find the task under "Aufgaben" in the sidebar to edit it. */}
        <IconButton aria-label={editLabel} onClick={onOpen}>
          <EditIcon fontSize="small" />
        </IconButton>
        {taskText && (
          <IconButton
            aria-label={taskToggleLabel}
            aria-expanded={taskOpen}
            onClick={() => setTaskOpen((v) => !v)}
          >
            {taskOpen ? (
              <ExpandLessIcon fontSize="small" />
            ) : (
              <ExpandMoreIcon fontSize="small" />
            )}
          </IconButton>
        )}
        <IconButton aria-label={removeLabel} color="error" onClick={onRemove}>
          <DeleteIcon fontSize="small" />
        </IconButton>
        <IconButton
          aria-label={dragHandleLabel}
          {...attributes}
          {...listeners}
          sx={{ cursor: 'grab', touchAction: 'none' }}
        >
          <DragIndicatorIcon fontSize="small" />
        </IconButton>
      </Box>
      {/* Read-only task preview. SafeHtml sanitises the
          stored rich text; the preview carries no editable control. */}
      <Collapse in={taskOpen} timeout="auto" unmountOnExit>
        <Box sx={{ px: 4, pb: 1.5, bgcolor: 'action.hover' }}>
          <Typography
            variant="caption"
            sx={{ color: 'text.secondary', fontWeight: 600, display: 'block' }}
          >
            {taskLabel}
          </Typography>
          <SafeHtml html={taskText} dir={taskDir} />
        </Box>
      </Collapse>
    </Box>
  );
};

// --------------------------------------------------------------------------
// Station row: expandable; carries its own challenge list/dnd.
// --------------------------------------------------------------------------
interface SortableStationRowProps {
  link: RallyeStationLink;
  index: number;
  label: string;
  secondary?: string;
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onRemove: () => void;
  onAddChallenge: () => void;
  onEditCopy: () => void;
  dragHandleLabel: string;
  removeLabel: string;
  expandLabel: string;
  children: React.ReactNode;
}

// The station row at 320 px: five icon buttons that cannot shrink would take
// the whole width and leave the name 0 px. Below sm the row wraps and the
// buttons take a line of their own under the name. From sm on it is one line.
// The DOM order, and so the keyboard order, does not change.
const stationRowSx = {
  display: 'flex',
  alignItems: 'center',
  gap: 2,
  rowGap: { xs: 0.5, sm: 2 },
  flexWrap: { xs: 'wrap', sm: 'nowrap' },
  px: 2,
  py: 1.5,
} as const;
const stationActionsSx = {
  flexShrink: 0,
  flexBasis: { xs: '100%', sm: 'auto' },
  justifyContent: { xs: 'flex-end', sm: 'flex-start' },
} as const;

// Presentational station header (badge + name/address + metric columns).
// Shared by the sortable row and the drag overlay so the drag preview keeps
// the exact same look and size.
const StationHeaderContent: React.FC<{
  link: RallyeStationLink;
  index: number;
  label: string;
  secondary?: string;
}> = ({ link, index, label, secondary }) => {
  const { t } = useTranslation('content');
  const metric = (labelText: string, value: number, icon?: React.ReactNode) => (
    <Stack spacing={0.25} sx={{ alignItems: 'center', minWidth: 72 }}>
      <Typography variant="caption" color="text.secondary" noWrap>
        {labelText}
      </Typography>
      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
        {icon}
        <Typography variant="h6" component="span" sx={{ fontWeight: 600, lineHeight: 1 }}>
          {value}
        </Typography>
      </Stack>
    </Stack>
  );
  return (
    <>
      <Box
        sx={{
          flexShrink: 0,
          width: 32,
          height: 32,
          borderRadius: '50%',
          bgcolor: 'primary.main',
          color: 'primary.contrastText',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontWeight: 600,
          fontSize: '0.95rem',
        }}
      >
        {index + 1}
      </Box>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        {/* subtitle1 defaults to an h6 element (MUI's mapping), jumping levels
            under the page's h1 and the section's h2. The component prop sets
            h3 and the variant keeps the look. */}
        <Typography variant="subtitle1" component="h3" noWrap sx={{ fontWeight: 500 }}>
          {label}
        </Typography>
        {secondary && (
          <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', minWidth: 0 }}>
            <PlaceIcon sx={{ fontSize: 16, color: 'text.secondary', flexShrink: 0 }} />
            <Typography variant="body2" color="text.secondary" noWrap>
              {secondary}
            </Typography>
          </Stack>
        )}
      </Box>
      <Stack
        direction="row"
        spacing={3}
        sx={{ display: { xs: 'none', sm: 'flex' }, flexShrink: 0 }}
      >
        {metric(t('composition.colChallenges'), link.challenge_count)}
        {metric(
          t('composition.colPoints'),
          link.total_points,
          <EmojiEventsOutlinedIcon sx={{ fontSize: 18, color: 'primary.main' }} />,
        )}
        {metric(
          t('composition.colTime'),
          link.total_duration_minutes,
          <ScheduleIcon sx={{ fontSize: 18, color: 'primary.main' }} />,
        )}
      </Stack>
    </>
  );
};

const SortableStationRow: React.FC<SortableStationRowProps> = ({
  link,
  index,
  label,
  secondary,
  expanded,
  onToggle,
  onOpen,
  onRemove,
  onAddChallenge,
  onEditCopy,
  dragHandleLabel,
  removeLabel,
  expandLabel,
  children,
}) => {
  const { t } = useTranslation('content');
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: link.station.id,
  });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };
  // While this row is the one being dragged it is hidden (opacity 0) but keeps
  // its real height as the placeholder; a faithful clone is rendered in the
  // DragOverlay, so the layout doesn't shift and nothing squashes/stretches.
  return (
    <Card
      ref={setNodeRef}
      style={style}
      variant="outlined"
      sx={{ mb: 1.5, borderRadius: 2, opacity: isDragging ? 0 : 1 }}
    >
      <Box sx={stationRowSx}>
        <StationHeaderContent link={link} index={index} label={label} secondary={secondary} />
        <Stack direction="row" spacing={0.5} sx={stationActionsSx}>
          <SharedActions count={link.station.usage_count ?? 0} onOpen={onOpen} onEditCopy={onEditCopy} />
          {/* Two stations carry the same wording, so each name holds the title. */}
          <IconButton aria-label={`${removeLabel}: ${label}`} color="error" onClick={onRemove}>
            <DeleteIcon fontSize="small" />
          </IconButton>
          <IconButton aria-label={`${t('composition.editStation')}: ${label}`} onClick={onOpen}>
            <EditIcon fontSize="small" />
          </IconButton>
          {/* One name per station: the wording plus the station's title. */}
          <IconButton
            aria-label={`${expandLabel}: ${label}`}
            aria-expanded={expanded}
            onClick={onToggle}
          >
            {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          </IconButton>
          <IconButton
            aria-label={dragHandleLabel}
            {...attributes}
            {...listeners}
            sx={{ cursor: 'grab', touchAction: 'none' }}
          >
            <DragIndicatorIcon />
          </IconButton>
        </Stack>
      </Box>
      <Collapse in={expanded} timeout="auto" unmountOnExit>
        <Box sx={{ pb: 1, bgcolor: 'action.hover' }}>
          {children}
          <Button
            size="small"
            startIcon={<AddIcon />}
            onClick={onAddChallenge}
            sx={{ mt: 1, ml: 4 }}
          >
            {t('composition.addChallenge')}
          </Button>
        </Box>
      </Collapse>
    </Card>
  );
};

const RallyeDetail: React.FC = () => {
  const { rallyeId } = useParams<{ rallyeId: string }>();
  const { user } = useAuth();
  const { t, i18n } = useTranslation('content');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const arrival = useLocation().state as { created?: boolean; challengeCreated?: boolean } | null;
  // Set by ChallengeCreate's create-and-link round trip and by a station's own
  // "back" link: the rallye-station whose challenge list should stay open.
  const openLinkId = searchParams.get('open');

  const [rallye, setRallye] = useState<Rallye | null>(null);
  const [links, setLinks] = useState<RallyeStationLink[]>([]);
  const [title, setTitle] = useState<MultilingualString>({});
  const [description, setDescription] = useState<MultilingualString>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // A short word after a save or the publish switch. It names the
  // publication when the save changed it.
  // RallyeCreate and ChallengeCreate navigate here with a state, so the page
  // that follows says what happened (the pattern of StationDetail).
  const [notice, setNotice] = useState<string | null>(() =>
    arrival?.created
      ? t('rallye.create.success')
      : arrival?.challengeCreated
        ? t('challenge.create.success')
        : null,
  );
  // "Als Kopie bearbeiten" asks first.
  const [copyTarget, setCopyTarget] = useState<RallyeStationLink | null>(null);
  const [saving, setSaving] = useState(false);
  // The publish switch is a field of the form: it moves this value and
  // "Speichern" sends it. `rallye.is_published` stays what the server holds.
  const [published, setPublished] = useState(false);
  // The back link asks before it drops unsaved changes, as on a station.
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [reordering, setReordering] = useState(false);
  const [activeStationId, setActiveStationId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [toUnlink, setToUnlink] = useState<RallyeStationLink | null>(null);
  const [unlinking, setUnlinking] = useState(false);

  // Per-station challenge state, keyed by the rallye-station link_id.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [challenges, setChallenges] = useState<Record<string, RallyeStationChallengeLink[]>>({});
  const [reorderingChallenges, setReorderingChallenges] = useState<Record<string, boolean>>({});
  // The rallye-station currently importing/adding a challenge.
  const [addChallengeFor, setAddChallengeFor] = useState<RallyeStationLink | null>(null);
  const [importChallengeFor, setImportChallengeFor] = useState<RallyeStationLink | null>(null);
  const [toUnlinkChallenge, setToUnlinkChallenge] = useState<{
    station: RallyeStationLink;
    challenge: RallyeStationChallengeLink;
  } | null>(null);
  const [unlinkingChallenge, setUnlinkingChallenge] = useState(false);
  // Revealed on an incomplete save click. The field itself stays untouched
  // until then.
  const [showErrors, setShowErrors] = useState(false);
  const titleRef = useRef<MultilingualFieldHandle>(null);

  const orgSlug = user?.organization_slug;

  // "Als Kopie bearbeiten": this rallye gets its own copy, which opens
  // for editing; every other rallye keeps the original.
  const handleEditStationAsCopy = async (link: RallyeStationLink) => {
    if (!orgSlug || !rallyeId) return;
    try {
      const updated = await copyRallyeStation(orgSlug, rallyeId, link.link_id);
      navigate(`/stations/${updated.station.id}?rallyeId=${rallyeId}`);
    } catch {
      setError(t('composition.copyFailed'));
    }
  };
  const handleEditChallengeAsCopy = async (
    stationLink: RallyeStationLink,
    challengeLink: RallyeStationChallengeLink,
  ) => {
    if (!orgSlug || !rallyeId) return;
    try {
      const updated = await copyRallyeStationChallenge(
        orgSlug,
        rallyeId,
        stationLink.link_id,
        challengeLink.challenge.id,
      );
      navigate(`/challenges/${updated.challenge.id}?rallyeId=${rallyeId}`);
    } catch {
      setError(t('composition.copyFailed'));
    }
  };
  const isOrgAdmin = user?.role === 'org_admin';
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = (ml: MultilingualString) => localizedText(ml, activeLang);

  const load = useCallback(async () => {
    if (!orgSlug || !rallyeId) return;
    setLoading(true);
    setError(null);
    try {
      const [r, s] = await Promise.all([
        getRallye(orgSlug, rallyeId),
        listRallyeStations(orgSlug, rallyeId),
      ]);
      setRallye(r);
      setTitle(r.title);
      setDescription(r.description);
      setPublished(r.is_published);
      setLinks([...s.items].sort((a, b) => a.sort_order - b.sort_order));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, rallyeId]);

  useEffect(() => {
    void load();
    setDialog(null);
    setDialogBusy(false);
  }, [load]);

  const metrics = useMemo(() => {
    let challengeCount = 0;
    let points = 0;
    let duration = 0;
    for (const l of links) {
      challengeCount += l.challenge_count;
      points += l.total_points;
      duration += l.total_duration_minutes;
    }
    return { stations: links.length, challenges: challengeCount, points, duration };
  }, [links]);

  const isDirty =
    !!rallye &&
    (JSON.stringify(title) !== JSON.stringify(rallye.title) ||
      JSON.stringify(description) !== JSON.stringify(rallye.description) ||
      published !== rallye.is_published);
  const deTitleMissing = !title.de?.trim();

  const loadChallenges = useCallback(
    async (link: RallyeStationLink) => {
      if (!orgSlug || !rallyeId) return;
      try {
        const resp = await listRallyeStationChallenges(orgSlug, rallyeId, link.link_id);
        setChallenges((prev) => ({
          ...prev,
          [link.link_id]: [...resp.items].sort((a, b) => a.sort_order - b.sort_order),
        }));
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : String(err));
      }
    },
    [orgSlug, rallyeId],
  );

  const handleToggleExpand = (link: RallyeStationLink) => {
    const next = !expanded[link.link_id];
    setExpanded((prev) => ({ ...prev, [link.link_id]: next }));
    if (next && challenges[link.link_id] === undefined) {
      void loadChallenges(link);
    }
  };

  // A station opened via ?open= (the create-and-link round trip from
  // ChallengeCreate) stays expanded instead of landing collapsed. Applied
  // once per value. A ref, not `expanded` itself, guards it, otherwise
  // collapsing the station later (expanded[openLinkId] turns false) would
  // re-run this effect and expand it right back.
  const consumedOpenRef = useRef<string | null>(null);
  useEffect(() => {
    if (!openLinkId || consumedOpenRef.current === openLinkId) return;
    const link = links.find((l) => l.link_id === openLinkId);
    if (!link) return;
    consumedOpenRef.current = openLinkId;
    setExpanded((prev) => ({ ...prev, [openLinkId]: true }));
    if (challenges[openLinkId] === undefined) {
      void loadChallenges(link);
    }
  }, [openLinkId, links, challenges, loadChallenges]);

  const handleSave = async () => {
    if (!orgSlug || !rallyeId || !rallye) return;
    if (deTitleMissing) {
      setShowErrors(true);
      titleRef.current?.focus();
      return;
    }
    const publicationChanged = published !== rallye.is_published;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateRallye(orgSlug, rallyeId, {
        title,
        description,
        is_published: published,
      });
      setRallye(updated);
      setPublished(updated.is_published);
      if (publicationChanged) {
        setNotice(
          t(updated.is_published ? 'rallye.detail.publishedNow' : 'rallye.detail.unpublishedNow'),
        );
      } else {
        setNotice(t('saved'));
      }
    } catch (err) {
      // The form, the switch included, stays as the creator left it.
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!orgSlug || !rallyeId) return;
    setDialogBusy(true);
    try {
      await deleteRallye(orgSlug, rallyeId);
      navigate('/rallyes');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setDialogBusy(false);
      setDialog(null);
    }
  };

  const handleDuplicate = async () => {
    if (!orgSlug || !rallyeId) return;
    setDialogBusy(true);
    try {
      const copy = await duplicateRallye(orgSlug, rallyeId);
      navigate(`/rallyes/${copy.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setDialogBusy(false);
      setDialog(null);
    }
  };

  const handleImportStation = async (stationId: string) => {
    if (!orgSlug || !rallyeId) return;
    await importStation(orgSlug, rallyeId, { station_id: stationId });
    setImportOpen(false);
    await load();
  };

  const handleUnlink = async () => {
    if (!orgSlug || !rallyeId || !toUnlink) return;
    setUnlinking(true);
    try {
      await unlinkStation(orgSlug, rallyeId, toUnlink.station.id);
      setLinks((prev) => prev.filter((l) => l.link_id !== toUnlink.link_id));
      setToUnlink(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setUnlinking(false);
    }
  };

  const handleImportChallenge = async (challengeId: string) => {
    if (!orgSlug || !rallyeId || !importChallengeFor) return;
    const target = importChallengeFor;
    await importChallengeToRallyeStation(orgSlug, rallyeId, target.link_id, {
      challenge_id: challengeId,
    });
    setImportChallengeFor(null);
    await loadChallenges(target);
    await load();
  };

  const handleUnlinkChallenge = async () => {
    if (!orgSlug || !rallyeId || !toUnlinkChallenge) return;
    const { station, challenge } = toUnlinkChallenge;
    setUnlinkingChallenge(true);
    try {
      await unlinkRallyeStationChallenge(orgSlug, rallyeId, station.link_id, challenge.challenge.id);
      setChallenges((prev) => ({
        ...prev,
        [station.link_id]: (prev[station.link_id] ?? []).filter(
          (l) => l.link_id !== challenge.link_id,
        ),
      }));
      setToUnlinkChallenge(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setUnlinkingChallenge(false);
    }
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    setActiveStationId(null);
    const { active, over } = event;
    if (!over || active.id === over.id || !orgSlug || !rallyeId) return;
    const ids = links.map((l) => l.station.id);
    const from = ids.indexOf(String(active.id));
    const to = ids.indexOf(String(over.id));
    if (from === -1 || to === -1) return;
    const nextIds = [...ids];
    nextIds.splice(from, 1);
    nextIds.splice(to, 0, String(active.id));

    const prev = links;
    const reordered = nextIds
      .map((id) => links.find((l) => l.station.id === id))
      .filter((l): l is RallyeStationLink => !!l);
    setLinks(reordered);
    setReordering(true);
    setError(null);
    try {
      const resp = await reorderStations(orgSlug, rallyeId, nextIds);
      setLinks([...resp.items].sort((a, b) => a.sort_order - b.sort_order));
    } catch (err) {
      setLinks(prev);
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setReordering(false);
    }
  };

  const handleChallengeDragEnd = async (link: RallyeStationLink, event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id || !orgSlug || !rallyeId) return;
    const current = challenges[link.link_id] ?? [];
    const ids = current.map((l) => l.challenge.id);
    const from = ids.indexOf(String(active.id));
    const to = ids.indexOf(String(over.id));
    if (from === -1 || to === -1) return;
    const nextIds = [...ids];
    nextIds.splice(from, 1);
    nextIds.splice(to, 0, String(active.id));

    const reordered = nextIds
      .map((id) => current.find((l) => l.challenge.id === id))
      .filter((l): l is RallyeStationChallengeLink => !!l);
    setChallenges((prev) => ({ ...prev, [link.link_id]: reordered }));
    setReorderingChallenges((prev) => ({ ...prev, [link.link_id]: true }));
    setError(null);
    try {
      const resp = await reorderRallyeStationChallenges(orgSlug, rallyeId, link.link_id, nextIds);
      setChallenges((prev) => ({
        ...prev,
        [link.link_id]: [...resp.items].sort((a, b) => a.sort_order - b.sort_order),
      }));
    } catch (err) {
      setChallenges((prev) => ({ ...prev, [link.link_id]: current }));
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setReorderingChallenges((prev) => ({ ...prev, [link.link_id]: false }));
    }
  };

  if (loading) return <LoadingSpinner />;
  if (!rallye) {
    return (
      <Box>
        <ErrorAlert message={error ?? 'Rallye not found'} onRetry={load} />
      </Box>
    );
  }

  return (
    <Box>
      <BackLink
        to="/rallyes"
        label={t('rallye.detail.backToList')}
        onClick={(event) => {
          if (!isDirty) return;
          event.preventDefault();
          setLeaveOpen(true);
        }}
      />
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{ justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, mb: 3 }}
      >
        <Box>
          <Typography variant="h2" component="h1">
            {titleOf(rallye.title) || t('rallye.detail.title')}
          </Typography>
          <Chip
            label={
              rallye.is_published ? t('rallye.list.filter.published') : t('rallye.list.filter.draft')
            }
            color={rallye.is_published ? 'success' : 'default'}
            size="small"
            sx={{ mt: 1 }}
          />
        </Box>
        <Stack direction="row" spacing={1}>
          <Button
            startIcon={<ContentCopyIcon />}
            variant="outlined"
            onClick={() => setDialog('duplicate')}
          >
            {t('rallye.detail.duplicate')}
          </Button>
          <Button
            startIcon={<DeleteIcon />}
            color="error"
            variant="outlined"
            onClick={() => setDialog('delete')}
          >
            {t('rallye.detail.archive')}
          </Button>
        </Stack>
      </Stack>

      {error && <ErrorAlert message={error} />}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr 1fr', sm: 'repeat(4, 1fr)' },
          gap: 2,
          mb: 3,
        }}
      >
        {[
          { label: t('composition.metrics.stations'), value: metrics.stations, accent: 'primary.main' },
          { label: t('composition.metrics.challenges'), value: metrics.challenges, accent: 'tertiary.main' },
          { label: t('composition.metrics.points'), value: metrics.points, accent: 'secondary.dark' },
          { label: t('composition.metrics.duration'), value: metrics.duration, accent: 'primary.main' },
        ].map((m) => (
          // The accent lives on the tile's left edge, never on the number. As a
          // text colour, `secondary.dark` and `tertiary.main` miss 4.5:1 on the
          // card surface; a 4px border carries the same per-tile distinction and
          // is a non-text UI element.
          <Card
            key={m.label}
            variant="outlined"
            sx={{ borderRadius: 2, borderLeft: '4px solid', borderLeftColor: m.accent }}
          >
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="caption" color="text.secondary">
                {m.label}
              </Typography>
              <Typography
                variant="h4"
                component="div"
                sx={{ fontWeight: 700, color: 'text.primary', lineHeight: 1.1, mt: 0.5 }}
              >
                {m.value}
              </Typography>
            </CardContent>
          </Card>
        ))}
      </Box>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Stack spacing={3}>
            <MultilingualField
              ref={titleRef}
              value={title}
              onChange={setTitle}
              label={t('rallye.create.titleLabel')}
              required
              showErrors={showErrors}
            />
            <MultilingualField
              value={description}
              onChange={setDescription}
              label={t('rallye.create.descriptionLabel')}
              richText
            />
            <Typography variant="caption" color="text.secondary">
              {t('rallye.detail.languagesHint')}
              {isOrgAdmin && (
                <>
                  {' '}
                  <Link component={RouterLink} to="/admin/settings">
                    {t('rallye.detail.languagesHintLink')}
                  </Link>
                </>
              )}
            </Typography>
            <Divider />
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}
            >
              {/* The label names the state and stays put. Naming the *action*
                  ("Veröffentlichen" / "Veröffentlichung zurückziehen") would
                  flip it with the switch, and read together with the switch
                  position it would say the opposite of the truth. The switch
                  position carries on or off, and the helper text says what
                  switching off costs. */}
              <Stack spacing={0.5}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={published}
                      onChange={(e) => setPublished(e.target.checked)}
                      disabled={saving}
                    />
                  }
                  label={t('rallye.detail.publishedLabel')}
                />
                <Typography variant="caption" color="text.secondary">
                  {published
                    ? t('rallye.detail.unpublishConsequence')
                    : t('rallye.detail.publishConsequence')}
                </Typography>
              </Stack>
              <Button
                variant="contained"
                onClick={handleSave}
                disabled={!isDirty || saving}
              >
                {t('actions.save', { ns: 'common' })}
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
            <Typography variant="h5" component="h2">
              {t('composition.stationsAndChallenges')}
            </Typography>
            <IconButton
              color="primary"
              onClick={() => setAddOpen(true)}
              aria-label={t('station.list.addButton')}
            >
              <AddIcon />
            </IconButton>
          </Stack>

          {links.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t('station.list.empty')}
            </Typography>
          ) : (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              modifiers={[restrictToVerticalAxis, restrictToParentElement]}
              accessibility={sortableA11y(
                links.map((l) => ({ id: l.station.id, label: titleOf(l.station.title) || l.station.id })),
                t,
              )}
              onDragStart={(e: DragStartEvent) => setActiveStationId(String(e.active.id))}
              onDragEnd={handleDragEnd}
              onDragCancel={() => setActiveStationId(null)}
            >
              <SortableContext
                items={links.map((l) => l.station.id)}
                strategy={verticalListSortingStrategy}
              >
                <Box sx={{ opacity: reordering ? 0.7 : 1, transition: 'opacity 0.15s' }}>
                  {links.map((l, idx) => {
                    const stationChallenges = challenges[l.link_id];
                    return (
                      <SortableStationRow
                        key={l.link_id}
                        link={l}
                        index={idx}
                        label={titleOf(l.station.title) || l.station.id}
                        secondary={l.station.address ?? excerpt(titleOf(l.station.description))}
                        expanded={!!expanded[l.link_id]}
                        onToggle={() => handleToggleExpand(l)}
                        onOpen={() => navigate(`/stations/${l.station.id}?rallyeId=${rallyeId}`)}
                        onRemove={() => setToUnlink(l)}
                        onEditCopy={() => setCopyTarget(l)}
                        onAddChallenge={() => setAddChallengeFor(l)}
                        dragHandleLabel={t('station.list.reorderHandle')}
                        removeLabel={t('composition.unlinkStation')}
                        expandLabel={t('composition.toggleChallenges')}
                      >
                        {stationChallenges === undefined ? (
                          <LoadingSpinner />
                        ) : stationChallenges.length === 0 ? (
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ px: 2, py: 1 }}
                          >
                            {t('composition.noChallenges')}
                          </Typography>
                        ) : (
                          <DndContext
                            sensors={sensors}
                            collisionDetection={closestCenter}
                            modifiers={[restrictToVerticalAxis, restrictToParentElement]}
                            accessibility={sortableA11y(
                              stationChallenges.map((c) => ({
                                id: c.challenge.id,
                                label: titleOf(c.challenge.title) || c.challenge.id,
                              })),
                              t,
                            )}
                            onDragEnd={(e) => handleChallengeDragEnd(l, e)}
                          >
                            <SortableContext
                              items={stationChallenges.map((c) => c.challenge.id)}
                              strategy={verticalListSortingStrategy}
                            >
                              <Box
                                sx={{
                                  opacity: reorderingChallenges[l.link_id] ? 0.7 : 1,
                                  transition: 'opacity 0.15s',
                                }}
                              >
                                {stationChallenges.map((c, cIdx) => {
                                  const pts =
                                    typeof c.challenge.scoring_rules?.max_points === 'number'
                                      ? c.challenge.scoring_rules.max_points
                                      : 0;
                                  const time = c.challenge.time_estimate_minutes ?? 0;
                                  const task = resolveTaskText(c.challenge.instructions, activeLang);
                                  return (
                                    <SortableChallengeRow
                                      key={c.link_id}
                                      link={c}
                                      index={cIdx}
                                      label={titleOf(c.challenge.title) || c.challenge.id}
                                      typeLabel={t(`challenge.types.${c.challenge.challenge_type}`)}
                                      metrics={t('composition.challengeMetrics', {
                                        points: pts,
                                        minutes: time,
                                      })}
                                      taskText={task.text}
                                      taskDir={dirAttr(task.locale)}
                                      taskLabel={t('composition.taskLabel')}
                                      taskToggleLabel={t('composition.toggleTask')}
                                      onRemove={() =>
                                        setToUnlinkChallenge({ station: l, challenge: c })
                                      }
                                      onOpen={() =>
                                        navigate(`/challenges/${c.challenge.id}?rallyeId=${rallyeId}`)
                                      }
                                      onEditCopy={() => void handleEditChallengeAsCopy(l, c)}
                                      dragHandleLabel={t('station.detail.challengeReorderHandle')}
                                      removeLabel={t('composition.removeChallenge')}
                                      editLabel={t('composition.editChallenge')}
                                    />
                                  );
                                })}
                              </Box>
                            </SortableContext>
                          </DndContext>
                        )}
                      </SortableStationRow>
                    );
                  })}
                </Box>
              </SortableContext>
              <DragOverlay>
                {(() => {
                  if (!activeStationId) return null;
                  const idx = links.findIndex((l) => l.station.id === activeStationId);
                  const l = links[idx];
                  if (!l) return null;
                  const isExp = !!expanded[l.link_id];
                  const chs = challenges[l.link_id];
                  // A non-interactive clone of the row so the drag preview looks
                  // exactly like the real card — icons included, and expanded
                  // stations stay expanded.
                  return (
                    <Card
                      variant="outlined"
                      sx={{ borderRadius: 2, boxShadow: 6, pointerEvents: 'none' }}
                    >
                      <Box sx={stationRowSx}>
                        <StationHeaderContent
                          link={l}
                          index={idx}
                          label={titleOf(l.station.title) || l.station.id}
                          secondary={l.station.address ?? excerpt(titleOf(l.station.description))}
                        />
                        <Stack direction="row" spacing={0.5} sx={stationActionsSx}>
                          <IconButton color="error" component="span">
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                          <IconButton component="span">
                            <EditIcon fontSize="small" />
                          </IconButton>
                          <IconButton component="span">
                            {isExp ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                          </IconButton>
                          <IconButton component="span" sx={{ cursor: 'grabbing' }}>
                            <DragIndicatorIcon />
                          </IconButton>
                        </Stack>
                      </Box>
                      {isExp && (
                        <>
                          <Box sx={{ pb: 1, bgcolor: 'action.hover' }}>
                            {chs && chs.length > 0 ? (
                              chs.map((c, i) => (
                                <Box
                                  key={c.link_id}
                                  sx={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 1,
                                    py: 0.75,
                                    pl: 4,
                                    pr: 1,
                                  }}
                                >
                                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                                    <Typography variant="body2" noWrap>
                                      {`${i + 1}. ${titleOf(c.challenge.title) || c.challenge.id}`}
                                    </Typography>
                                    <Typography variant="caption" color="text.secondary">
                                      {t('composition.challengeMetrics', {
                                        points:
                                          typeof c.challenge.scoring_rules?.max_points === 'number'
                                            ? c.challenge.scoring_rules.max_points
                                            : 0,
                                        minutes: c.challenge.time_estimate_minutes ?? 0,
                                      })}
                                    </Typography>
                                  </Box>
                                  <Chip
                                    label={t(`challenge.types.${c.challenge.challenge_type}`)}
                                    size="small"
                                  />
                                  <IconButton component="span" color="error">
                                    <DeleteIcon fontSize="small" />
                                  </IconButton>
                                  <IconButton component="span" sx={{ cursor: 'grabbing' }}>
                                    <DragIndicatorIcon fontSize="small" />
                                  </IconButton>
                                </Box>
                              ))
                            ) : (
                              <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 1 }}>
                                {t('composition.noChallenges')}
                              </Typography>
                            )}
                            <Button size="small" startIcon={<AddIcon />} sx={{ mt: 1, ml: 4 }}>
                              {t('composition.addChallenge')}
                            </Button>
                          </Box>
                        </>
                      )}
                    </Card>
                  );
                })()}
              </DragOverlay>
            </DndContext>
          )}
        </CardContent>
      </Card>

      <AddChoiceDialog
        open={addOpen}
        title={t('station.addPopup.title')}
        subtitle={t('station.addPopup.subtitle')}
        createLabel={t('station.addPopup.createNew')}
        createDescription={t('station.addPopup.createNewDesc')}
        importLabel={t('station.addPopup.importExisting')}
        importDescription={t('station.addPopup.importExistingDesc')}
        onCreateNew={() => {
          setAddOpen(false);
          navigate(`/stations/new?rallyeId=${rallyeId}`);
        }}
        onImportExisting={() => {
          setAddOpen(false);
          setImportOpen(true);
        }}
        onCancel={() => setAddOpen(false)}
      />

      <ImportStationDialog
        open={importOpen}
        linkedStationIds={links.map((l) => l.station.id)}
        onCancel={() => setImportOpen(false)}
        onImport={handleImportStation}
      />

      <AddChoiceDialog
        open={!!addChallengeFor}
        title={t('challenge.addPopup.title')}
        subtitle={t('challenge.addPopup.subtitle')}
        createLabel={t('challenge.addPopup.createNew')}
        createDescription={t('challenge.addPopup.createNewDesc')}
        importLabel={t('challenge.addPopup.importExisting')}
        importDescription={t('challenge.addPopup.importExistingDesc')}
        onCreateNew={() => {
          const target = addChallengeFor;
          setAddChallengeFor(null);
          if (target) {
            navigate(
              `/challenges/new?rallyeId=${rallyeId}&rallyeStationId=${target.link_id}`,
            );
          }
        }}
        onImportExisting={() => {
          setImportChallengeFor(addChallengeFor);
          setAddChallengeFor(null);
        }}
        onCancel={() => setAddChallengeFor(null)}
      />

      <ImportChallengeDialog
        open={!!importChallengeFor}
        stationId={importChallengeFor?.station.id}
        linkedChallengeIds={
          importChallengeFor
            ? (challenges[importChallengeFor.link_id] ?? []).map((c) => c.challenge.id)
            : []
        }
        onCancel={() => setImportChallengeFor(null)}
        onImport={handleImportChallenge}
      />

      <Snackbar
        open={notice !== null}
        autoHideDuration={4000}
        onClose={(_, reason) => {
          if (reason !== 'clickaway') setNotice(null);
        }}
        message={notice}
      />
      <ConfirmDialog
        open={leaveOpen}
        title={t('unsaved.title')}
        message={t('unsaved.message')}
        confirmLabel={t('unsaved.leave')}
        cancelLabel={t('unsaved.stay')}
        destructive
        onConfirm={() => navigate('/rallyes')}
        onCancel={() => setLeaveOpen(false)}
      />
      <ConfirmDialog
        open={copyTarget !== null}
        title={t('composition.editAsCopy')}
        message={t('composition.editAsCopyConfirm')}
        confirmLabel={t('composition.editAsCopyConfirmButton')}
        onConfirm={() => {
          const link = copyTarget;
          setCopyTarget(null);
          if (link) void handleEditStationAsCopy(link);
        }}
        onCancel={() => setCopyTarget(null)}
      />

      <ConfirmDialog
        open={dialog === 'delete'}
        title={t('rallye.detail.archive')}
        message={t('rallye.detail.archiveConfirm')}
        destructive
        loading={dialogBusy}
        confirmLabel={t('archive.archive')}
        onConfirm={handleDelete}
        onCancel={() => setDialog(null)}
      />

      <ConfirmDialog
        open={dialog === 'duplicate'}
        title={t('rallye.detail.duplicate')}
        message={t('rallye.detail.duplicateConfirm')}
        confirmLabel={t('rallye.detail.duplicate')}
        loading={dialogBusy}
        onConfirm={handleDuplicate}
        onCancel={() => setDialog(null)}
      />

      <ConfirmDialog
        open={!!toUnlink}
        title={t('composition.unlinkStation')}
        message={`${t('composition.unlinkStationConfirm')} ${t('composition.unlinkStationRunsNote')}`}
        destructive
        loading={unlinking}
        confirmLabel={t('composition.unlinkStation')}
        onConfirm={handleUnlink}
        onCancel={() => setToUnlink(null)}
      />

      <ConfirmDialog
        open={!!toUnlinkChallenge}
        title={t('composition.removeChallenge')}
        message={t('composition.removeChallengeConfirm')}
        destructive
        loading={unlinkingChallenge}
        confirmLabel={t('composition.removeChallenge')}
        onConfirm={handleUnlinkChallenge}
        onCancel={() => setToUnlinkChallenge(null)}
      />
    </Box>
  );
};

export default RallyeDetail;
