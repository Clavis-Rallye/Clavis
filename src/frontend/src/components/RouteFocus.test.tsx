import { describe, it, expect } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState, useEffect } from 'react';
import { MemoryRouter, Routes, Route, useNavigate } from 'react-router';
import RouteFocus from './RouteFocus';

// Settles past RouteFocus's own double requestAnimationFrame wait, with a
// margin, so a test can assert that nothing happened.
function flushAnimationFrames(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
}

function Nav({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      {label}
    </button>
  );
}

function PageWithHeading() {
  return (
    <main>
      <h1>With heading</h1>
      <Nav to="/no-heading" label="go-to-no-heading" />
    </main>
  );
}

function PageWithoutHeading() {
  return (
    <main>
      <p>No heading here</p>
      <Nav to="/with-heading" label="go-to-with-heading" />
    </main>
  );
}

function PageWithSearchParamNav() {
  const navigate = useNavigate();
  return (
    <main>
      <h1>Search page</h1>
      <button type="button" onClick={() => navigate('/with-heading?foo=bar')}>
        search-nav
      </button>
    </main>
  );
}

function StartPage({ to, label }: { to: string; label: string }) {
  return (
    <main>
      <Nav to={to} label={label} />
    </main>
  );
}

// Like the dashboard: the h1 renders only after the page's data has
// "loaded" — here 100 ms after mount, well past RouteFocus's two frames.
function LateHeadingPage() {
  const [showHeading, setShowHeading] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setShowHeading(true), 100);
    return () => window.clearTimeout(id);
  }, []);
  return (
    <main>
      {showHeading ? <h1>Late heading</h1> : <p>Still loading</p>}
    </main>
  );
}

// Same shape, but the heading mounts on a document event the test fires, so
// the test controls the exact moment it appears relative to the input's focus.
function LateHeadingWithFieldPage() {
  const [showHeading, setShowHeading] = useState(false);
  useEffect(() => {
    const reveal = () => setShowHeading(true);
    document.addEventListener('reveal-late-heading', reveal);
    return () => document.removeEventListener('reveal-late-heading', reveal);
  }, []);
  return (
    <main>
      {showHeading ? <h1>Late heading</h1> : null}
      <label htmlFor="late-field">Late field</label>
      <input id="late-field" />
    </main>
  );
}

describe('<RouteFocus>', () => {
  it('moves no focus on the first render of any page', async () => {
    render(
      <MemoryRouter initialEntries={['/with-heading']}>
        <RouteFocus />
        <Routes>
          <Route path="/with-heading" element={<PageWithHeading />} />
        </Routes>
      </MemoryRouter>,
    );

    await flushAnimationFrames();

    expect(document.activeElement).toBe(document.body);
  });

  it("focuses the new page's h1 after a navigation", async () => {
    render(
      <MemoryRouter initialEntries={['/no-heading']}>
        <RouteFocus />
        <Routes>
          <Route path="/with-heading" element={<PageWithHeading />} />
          <Route path="/no-heading" element={<PageWithoutHeading />} />
        </Routes>
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'go-to-with-heading' }));

    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'With heading' }));
    });
  });

  it('focuses main when the new page has no h1', async () => {
    render(
      <MemoryRouter initialEntries={['/with-heading']}>
        <RouteFocus />
        <Routes>
          <Route path="/with-heading" element={<PageWithHeading />} />
          <Route path="/no-heading" element={<PageWithoutHeading />} />
        </Routes>
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'go-to-no-heading' }));

    await waitFor(() => {
      expect(document.activeElement?.tagName).toBe('MAIN');
    });
  });

  it('focuses a heading that mounts 100 ms after the navigation', async () => {
    render(
      <MemoryRouter initialEntries={['/start']}>
        <RouteFocus />
        <Routes>
          <Route
            path="/start"
            element={<StartPage to="/late-heading" label="go-to-late-heading" />}
          />
          <Route path="/late-heading" element={<LateHeadingPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'go-to-late-heading' }));

    await waitFor(
      () => {
        expect(screen.getByRole('heading', { name: 'Late heading' })).toHaveFocus();
      },
      { timeout: 2000 },
    );
  });

  it('leaves an input focused when the heading mounts after the person focused it', async () => {
    render(
      <MemoryRouter initialEntries={['/start']}>
        <RouteFocus />
        <Routes>
          <Route
            path="/start"
            element={<StartPage to="/late-field" label="go-to-late-field" />}
          />
          <Route path="/late-field" element={<LateHeadingWithFieldPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'go-to-late-field' }));

    // While the heading is missing, the move parks the focus on main.
    await waitFor(() => {
      expect(document.activeElement?.tagName).toBe('MAIN');
    });

    const field = screen.getByLabelText('Late field');
    await user.click(field);
    expect(field).toHaveFocus();

    // The heading mounts while the field holds the focus.
    await act(async () => {
      document.dispatchEvent(new Event('reveal-late-heading'));
    });

    expect(screen.getByRole('heading', { name: 'Late heading' })).toBeInTheDocument();
    expect(field).toHaveFocus();
  });

  it('leaves focus in an open dialog when the route changes under it', async () => {
    function DialogWithNav() {
      const navigate = useNavigate();
      return (
        <div role="dialog">
          <button type="button" onClick={() => navigate('/with-heading')}>
            dialog nav button
          </button>
        </div>
      );
    }

    render(
      <MemoryRouter initialEntries={['/no-heading']}>
        <RouteFocus />
        {/* Mounted as a sibling of <Routes>, so it survives the route change
            underneath it — the way a real dialog sits above the page. The
            dialog's own button drives the navigation, so the click's natural
            focus move lands (and stays) inside the dialog, not on a control
            the route change then removes. */}
        <DialogWithNav />
        <Routes>
          <Route path="/with-heading" element={<PageWithHeading />} />
          <Route path="/no-heading" element={<PageWithoutHeading />} />
        </Routes>
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    const dialogButton = screen.getByRole('button', { name: 'dialog nav button' });
    await user.click(dialogButton);
    expect(document.activeElement).toBe(dialogButton);

    await flushAnimationFrames();

    expect(document.activeElement).toBe(dialogButton);
  });

  it('does not move focus on a search-param-only navigation', async () => {
    render(
      <MemoryRouter initialEntries={['/with-heading']}>
        <RouteFocus />
        <Routes>
          <Route path="/with-heading" element={<PageWithSearchParamNav />} />
        </Routes>
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    const searchNavButton = screen.getByRole('button', { name: 'search-nav' });
    await user.click(searchNavButton);

    await flushAnimationFrames();

    expect(document.activeElement).toBe(searchNavButton);
  });
});
