import { describe, it, expect, vi, afterEach } from 'vitest';
import { isQaRole, pageDevice, pageRole, rolesMatch } from './roles';

afterEach(() => {
  vi.unstubAllGlobals();
});

function pointer(coarse: boolean, width: number): void {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: coarse && query === '(pointer: coarse)', media: query }));
  vi.stubGlobal('innerWidth', width);
}

describe('pageRole', () => {
  it('reads the operator from the console path, except its login page', () => {
    expect(pageRole(null, '/instance')).toBe('operator');
    expect(pageRole(null, '/instance/audit')).toBe('operator');
    expect(pageRole(null, '/instance/login')).toBe('signed_out');
    expect(pageRole(null, '/instances')).toBe('signed_out');
  });

  it('reads the organization role from the session, else signed out', () => {
    expect(pageRole({ role: 'coordinator' }, '/dashboard')).toBe('coordinator');
    expect(pageRole({ role: 'guest' }, '/runs/1/participant/stations')).toBe('guest');
    expect(pageRole({ role: 'someone' }, '/dashboard')).toBe('signed_out');
    expect(pageRole(null, '/login')).toBe('signed_out');
  });
});

describe('rolesMatch', () => {
  it('matches equal roles, and a participant on a guest step', () => {
    expect(rolesMatch('operator', 'operator')).toBe(true);
    expect(rolesMatch('guest', 'participant')).toBe(true);
    expect(rolesMatch('participant', 'guest')).toBe(false);
    expect(rolesMatch('operator', 'signed_out')).toBe(false);
  });

  it('knows its seven roles', () => {
    expect(isQaRole('signed_out')).toBe(true);
    expect(isQaRole('admin')).toBe(false);
    expect(isQaRole(null)).toBe(false);
  });
});

describe('pageDevice', () => {
  it('calls a narrow window with a coarse pointer a phone', () => {
    pointer(true, 412);
    expect(pageDevice()).toBe('phone');
  });

  it('calls a fine pointer or a wide window a desktop', () => {
    pointer(false, 412);
    expect(pageDevice()).toBe('desktop');
    pointer(true, 1024);
    expect(pageDevice()).toBe('desktop');
  });

  it('calls a browser without matchMedia a desktop', () => {
    vi.stubGlobal('matchMedia', undefined);
    expect(pageDevice()).toBe('desktop');
  });
});
