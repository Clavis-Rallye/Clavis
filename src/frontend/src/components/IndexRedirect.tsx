import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router';

/**
 * An index redirect that never overtakes a navigation still on its way.
 *
 * BrowserRouter applies every location change inside `React.startTransition`,
 * so a tap's navigation can already have changed the browser's URL while this
 * route still renders for the old location. A plain `<Navigate replace>`
 * redirects unconditionally in its effect, so it would then replace the
 * tapped URL. This one navigates only while the browser's URL
 * still equals the location it rendered for. Otherwise the pending navigation
 * renders its own route.
 */
const IndexRedirect: React.FC<{ to: string }> = ({ to }) => {
  const navigate = useNavigate();
  const { pathname, search, hash } = useLocation();

  useEffect(() => {
    const { location } = window;
    if (location.pathname + location.search + location.hash !== pathname + search + hash) {
      return;
    }
    navigate(to, { replace: true });
  }, [navigate, to, pathname, search, hash]);

  return null;
};

export default IndexRedirect;
