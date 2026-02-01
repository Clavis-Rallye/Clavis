import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  AppBar,
  Box,
  Toolbar,
  Tooltip,
  Typography,
  IconButton,
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Divider,
  Collapse,
} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import MenuOpenIcon from '@mui/icons-material/MenuOpen';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import LogoutIcon from '@mui/icons-material/Logout';
import PersonOutlineIcon from '@mui/icons-material/PersonOutlined';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutlined';
import InsightsIcon from '@mui/icons-material/Insights';
import FlagIcon from '@mui/icons-material/Flag';
import PlaceIcon from '@mui/icons-material/Place';
import ExtensionIcon from '@mui/icons-material/Extension';
import SettingsIcon from '@mui/icons-material/Settings';
import PeopleIcon from '@mui/icons-material/People';
import ArticleIcon from '@mui/icons-material/Article';
import { useAuth } from '../auth/useAuth';
import type { Role } from '../auth/ProtectedRoute';
import { getOrg } from '../api/admin';
import { useInstanceConfig } from '../config/instanceConfig';
import { OrgContext } from '../contexts/OrgContext';
import { resolveContentLocales, type Locale } from '../i18n/contentLocales';
import logoSmall from '../assets/logos/logo-small.svg';
import { visuallyHiddenSx } from '../styles/visuallyHidden';

const DRAWER_WIDTH_EXPANDED = 240;
const DRAWER_WIDTH_COLLAPSED = 64;
const COLLAPSED_STORAGE_KEY = 'clavis_sidebar_collapsed';
const GROUPS_STORAGE_KEY = 'clavis_sidebar_groups';

type GroupKey = 'coordinator' | 'creator' | 'admin';

interface NavItem {
  text: string;
  icon: React.ReactNode;
  path: string;
  visible: boolean;
}

interface NavGroup {
  key: GroupKey;
  title: string;
  minRole: Role;
  items: NavItem[];
}

const AppShell: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const { t } = useTranslation('common');
  const { brand_name: brand } = useInstanceConfig();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(COLLAPSED_STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });
  const [openGroups, setOpenGroups] = useState<Record<GroupKey, boolean>>(() => {
    try {
      const raw = localStorage.getItem(GROUPS_STORAGE_KEY);
      if (raw) return { coordinator: true, creator: true, admin: true, ...JSON.parse(raw) };
    } catch {
      // ignore
    }
    return { coordinator: true, creator: true, admin: true };
  });
  const [orgName, setOrgName] = useState<string | null>(null);
  const [orgLanguages, setOrgLanguages] = useState<string[] | null>(null);
  const [orgRefreshKey, setOrgRefreshKey] = useState(0);
  const refreshOrg = useCallback(() => setOrgRefreshKey((k) => k + 1), []);

  useEffect(() => {
    if (!user?.organization_slug) return;
    let cancelled = false;
    getOrg(user.organization_slug)
      .then((org) => {
        if (!cancelled) {
          setOrgName(org.name);
          setOrgLanguages(org.languages);
        }
      })
      .catch(() => { /* fall back to brand name + default languages */ });
    return () => { cancelled = true; };
  }, [user?.organization_slug, orgRefreshKey]);

  const contentLanguages = useMemo<Locale[]>(
    () => (orgLanguages ? resolveContentLocales(orgLanguages) : ['de', 'en']),
    [orgLanguages],
  );

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_STORAGE_KEY, String(collapsed));
    } catch {
      // storage may be unavailable
    }
  }, [collapsed]);

  useEffect(() => {
    try {
      localStorage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(openGroups));
    } catch {
      // storage may be unavailable
    }
  }, [openGroups]);

  const handleDrawerToggle = () => setMobileOpen(!mobileOpen);
  const handleCollapseToggle = () => setCollapsed((v) => !v);
  const handleGroupToggle = (key: GroupKey) =>
    setOpenGroups((g) => ({ ...g, [key]: !g[key] }));

  const drawerWidth = collapsed ? DRAWER_WIDTH_COLLAPSED : DRAWER_WIDTH_EXPANDED;

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const goToDashboard = () => {
    navigate('/dashboard');
    setMobileOpen(false);
  };

  const ROLE_ORDER: Role[] = ['guest', 'participant', 'coordinator', 'creator', 'org_admin'];
  const meetsMin = (min: Role) =>
    !!user && ROLE_ORDER.indexOf(user.role) >= ROLE_ORDER.indexOf(min);

  // Map the user's role to the access label shown in the header. Falls back
  // to a generic label for participants and guests.
  const roleAccessLabel = (() => {
    switch (user?.role) {
      case 'org_admin':
        return t('roleAccess.org_admin');
      case 'creator':
        return t('roleAccess.creator');
      case 'coordinator':
        return t('roleAccess.coordinator');
      default:
        return t('roleAccess.default');
    }
  })();

  const groups: NavGroup[] = [
    {
      key: 'coordinator',
      title: t('nav.groups.coordination'),
      minRole: 'coordinator',
      items: [
        { text: t('nav.runs'), icon: <PlayCircleOutlineIcon />, path: '/runs', visible: meetsMin('coordinator') },
        { text: t('nav.analytics'), icon: <InsightsIcon />, path: '/analytics', visible: meetsMin('coordinator') },
      ],
    },
    {
      key: 'creator',
      title: t('nav.groups.creation'),
      minRole: 'creator',
      items: [
        { text: t('nav.rallyes'), icon: <FlagIcon />, path: '/rallyes', visible: meetsMin('creator') },
        { text: t('nav.stations'), icon: <PlaceIcon />, path: '/stations', visible: meetsMin('creator') },
        { text: t('nav.challenges'), icon: <ExtensionIcon />, path: '/challenges', visible: meetsMin('creator') },
      ],
    },
    {
      key: 'admin',
      title: t('nav.groups.administration'),
      minRole: 'org_admin',
      items: [
        { text: t('nav.users'), icon: <PeopleIcon />, path: '/admin/users', visible: meetsMin('org_admin') },
        { text: t('nav.logs'), icon: <ArticleIcon />, path: '/admin/logs', visible: meetsMin('org_admin') },
        { text: t('nav.settings'), icon: <SettingsIcon />, path: '/admin/settings', visible: meetsMin('org_admin') },
      ],
    },
  ];

  const accountItem: NavItem = {
    text: t('nav.account'),
    icon: <PersonOutlineIcon />,
    path: '/profile',
    visible: true,
  };

  const isItemActive = (path: string) => {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  const renderItem = (item: NavItem, isCollapsed: boolean, indent: boolean) => {
    const active = isItemActive(item.path);
    const button = (
      <ListItemButton
        onClick={() => {
          navigate(item.path);
          setMobileOpen(false);
        }}
        aria-label={isCollapsed ? item.text : undefined}
        aria-current={active ? 'page' : undefined}
        selected={active}
        sx={{
          justifyContent: isCollapsed ? 'center' : 'flex-start',
          pl: isCollapsed ? 1 : indent ? 3 : 2,
          pr: isCollapsed ? 1 : 2,
        }}
      >
        <ListItemIcon sx={{ minWidth: isCollapsed ? 0 : 36, justifyContent: 'center' }}>
          {item.icon}
        </ListItemIcon>
        {!isCollapsed && <ListItemText primary={item.text} />}
      </ListItemButton>
    );
    return (
      <ListItem key={item.path} disablePadding>
        {isCollapsed ? (
          <Tooltip title={item.text} placement="right">
            <Box sx={{ width: '100%' }}>{button}</Box>
          </Tooltip>
        ) : (
          button
        )}
      </ListItem>
    );
  };

  const renderDrawerContent = (isCollapsed: boolean) => (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Toolbar sx={{ minHeight: { xs: 64, sm: 72 } }} />
      <Box component="nav" aria-label={t('aria.mainNav')} sx={{ flexGrow: 1, overflowY: 'auto' }}>
        {groups
          .filter((group) => meetsMin(group.minRole) && group.items.some((i) => i.visible))
          .map((group, index) => {
            const visibleItems = group.items.filter((i) => i.visible);
            if (isCollapsed) {
              return (
                <React.Fragment key={group.key}>
                  {index > 0 && <Divider />}
                  <List disablePadding>
                    {visibleItems.map((item) => renderItem(item, true, false))}
                  </List>
                </React.Fragment>
              );
            }
            const open = openGroups[group.key];
            // The expanded group is a `div`, not a `ul`: its children are the group-header
            // button and the `Collapse`, and a `ul` may only contain `li`. The `ul` is the
            // inner List inside the `Collapse`, so a screen reader announces one list per
            // group with the item count. `component="div"` keeps the MuiList-root styles,
            // so the box is unchanged.
            return (
              <List key={group.key} component="div" disablePadding>
                <ListItemButton
                  onClick={() => handleGroupToggle(group.key)}
                  // The group's name is part of the button's name, so the three
                  // buttons do not all read "Gruppe ein-/ausblenden".
                  aria-label={t('aria.toggleGroupNamed', { group: group.title })}
                  aria-expanded={open}
                  sx={{ pl: 2, pr: 2, py: 1 }}
                >
                  <ListItemText
                    primary={group.title}
                    slotProps={{
                      primary: {
                        sx: { fontWeight: 700, color: 'text.primary', fontSize: '1rem' },
                      },
                    }}
                  />
                  {open ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
                </ListItemButton>
                <Collapse in={open} timeout="auto" unmountOnExit>
                  <List disablePadding>
                    {visibleItems.map((item) => renderItem(item, false, false))}
                  </List>
                </Collapse>
              </List>
            );
          })}
      </Box>
      <Divider />
      <List>
        {renderItem(accountItem, isCollapsed, false)}
        <ListItem disablePadding>
          {(() => {
            const button = (
              <ListItemButton
                onClick={handleLogout}
                aria-label={isCollapsed ? t('nav.logout') : undefined}
                sx={{
                  justifyContent: isCollapsed ? 'center' : 'flex-start',
                  pl: isCollapsed ? 1 : 2,
                  pr: isCollapsed ? 1 : 2,
                }}
              >
                <ListItemIcon sx={{ minWidth: isCollapsed ? 0 : 36, justifyContent: 'center' }}>
                  <LogoutIcon />
                </ListItemIcon>
                {!isCollapsed && <ListItemText primary={t('nav.logout')} />}
              </ListItemButton>
            );
            return isCollapsed ? (
              <Tooltip title={t('nav.logout')} placement="right">
                <Box sx={{ width: '100%' }}>{button}</Box>
              </Tooltip>
            ) : (
              button
            );
          })()}
        </ListItem>
      </List>
    </Box>
  );

  return (
    <OrgContext.Provider
      value={{ name: orgName, languages: contentLanguages, refresh: refreshOrg }}
    >
    <Box sx={{ display: 'flex' }}>
      <a href="#main-content" className="skip-link">{t('aria.skipToMain')}</a>
      <AppBar
        className="print-hidden"
        position="fixed"
        sx={{
          zIndex: (theme) => theme.zIndex.drawer + 1,
          borderBottom: '1px solid',
          borderColor: 'divider',
        }}
      >
        <Toolbar sx={{ minHeight: { xs: 64, sm: 72 }, gap: 1 }}>
          <IconButton
            color="inherit"
            aria-label={t('aria.openDrawer')}
            edge="start"
            onClick={handleDrawerToggle}
            sx={{ display: { sm: 'none' } }}
          >
            <MenuIcon />
          </IconButton>
          <IconButton
            color="inherit"
            aria-label={t(collapsed ? 'aria.expandSidebar' : 'aria.collapseSidebar')}
            edge="start"
            onClick={handleCollapseToggle}
            sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
          >
            {collapsed ? <MenuIcon /> : <MenuOpenIcon />}
          </IconButton>

          {/* Brand and greeting. They link back to the dashboard, which has no
              nav item of its own. The visible text is the name, so a voice
              command with the words on the screen finds the button. Where it
              leads follows as the last words of the name. */}
          <Box
            component="button"
            onClick={goToDashboard}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1.5,
              flexGrow: 1,
              minWidth: 0,
              border: 0,
              background: 'none',
              p: 0,
              cursor: 'pointer',
              textAlign: 'left',
              color: 'inherit',
              font: 'inherit',
            }}
          >
            <Box
              component="img"
              src={logoSmall}
              alt={brand}
              sx={{ width: 40, height: 36, flexShrink: 0 }}
            />
            <Box sx={{ minWidth: 0 }}>
              <Typography
                variant="h6"
                noWrap
                component="div"
                sx={{ fontWeight: 700, lineHeight: 1.25 }}
              >
                {t('welcomeBack', { name: user?.display_name ?? '' })}
              </Typography>
              <Typography
                variant="body2"
                noWrap
                component="div"
                sx={{ color: 'text.secondary', display: { xs: 'none', sm: 'block' } }}
              >
                {orgName ?? brand}
              </Typography>
            </Box>
            <Box component="span" sx={visuallyHiddenSx}>
              {t('nav.toDashboard')}
            </Box>
          </Box>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box sx={{ textAlign: 'right', display: { xs: 'none', sm: 'block' }, minWidth: 0 }}>
              {user?.email && (
                <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
                  {user.email}
                </Typography>
              )}
              <Typography variant="body2" noWrap sx={{ color: 'text.secondary' }}>
                {roleAccessLabel}
              </Typography>
            </Box>
          </Box>
        </Toolbar>
      </AppBar>

      {/* Mobile drawer. MUI gives the temporary variant's paper role dialog,
          so it carries the name of the navigation inside it. The permanent
          drawer below is no dialog and stays unnamed. */}
      <Drawer
        className="print-hidden"
        variant="temporary"
        open={mobileOpen}
        onClose={handleDrawerToggle}
        ModalProps={{ keepMounted: true }}
        slotProps={{ paper: { 'aria-label': t('aria.mainNav') } }}
        sx={{
          display: { xs: 'block', sm: 'none' },
          '& .MuiDrawer-paper': { boxSizing: 'border-box', width: DRAWER_WIDTH_EXPANDED },
        }}
      >
        {renderDrawerContent(false)}
      </Drawer>

      {/* Desktop drawer — collapsible */}
      <Drawer
        className="print-hidden"
        variant="permanent"
        sx={{
          display: { xs: 'none', sm: 'block' },
          width: drawerWidth,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            boxSizing: 'border-box',
            width: drawerWidth,
            overflowX: 'hidden',
            borderRight: '1px solid',
            borderColor: 'divider',
            transition: (theme) =>
              theme.transitions.create('width', {
                easing: theme.transitions.easing.sharp,
                duration: theme.transitions.duration.enteringScreen,
              }),
          },
        }}
        open
      >
        {renderDrawerContent(collapsed)}
      </Drawer>

      {/* tabindex -1 lets the skip link hand the focus here. It draws no
          outline of its own (index.css). */}
      <Box
        component="main"
        id="main-content"
        tabIndex={-1}
        sx={{
          flexGrow: 1,
          // A flex child defaults to min-width auto, so a wide table would grow
          // this box past the viewport and its own container would never
          // scroll. minWidth 0 lets the box shrink and moves the overflow inside.
          minWidth: 0,
          p: { xs: 2, sm: 3 },
          width: { sm: `calc(100% - ${drawerWidth}px)` },
          minHeight: '100vh',
          bgcolor: 'background.default',
          transition: (theme) =>
            theme.transitions.create('width', {
              easing: theme.transitions.easing.sharp,
              duration: theme.transitions.duration.enteringScreen,
            }),
        }}
      >
        <Toolbar className="print-hidden" sx={{ minHeight: { xs: 64, sm: 72 } }} />
        <Outlet />
      </Box>
    </Box>
    </OrgContext.Provider>
  );
};

export default AppShell;
