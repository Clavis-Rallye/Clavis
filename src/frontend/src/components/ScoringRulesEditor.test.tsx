import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import ScoringRulesEditor from './ScoringRulesEditor';
import '../i18n';

describe('ScoringRulesEditor', () => {
  it('renders max_points input for non-quiz types', () => {
    render(
      <ScoringRulesEditor
        value={{ max_points: 100 }}
        onChange={vi.fn()}
        challengeType="photo"
      />,
    );
    expect(screen.getByLabelText(/Maximale Punkte/)).toHaveValue(100);
    expect(screen.queryByText(/Automatisch bewerten/)).not.toBeInTheDocument();
  });

  it('shows validation error when max_points < 1', () => {
    render(
      <ScoringRulesEditor
        value={{ max_points: 0 }}
        onChange={vi.fn()}
        challengeType="text"
      />,
    );
    const input = screen.getByLabelText(/Maximale Punkte/);
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });

  it('calls onChange with numeric max_points when input changes', async () => {
    const onChange = vi.fn();
    render(
      <ScoringRulesEditor value={{}} onChange={onChange} challengeType="text" />,
    );
    const input = screen.getByLabelText(/Maximale Punkte/);
    await userEvent.type(input, '5');
    expect(onChange).toHaveBeenLastCalledWith({ max_points: 5 });
  });

  it('shows auto_approve checkbox only for quiz / qr_scan types', () => {
    const { rerender } = render(
      <ScoringRulesEditor
        value={{ max_points: 10 }}
        onChange={vi.fn()}
        challengeType="text"
      />,
    );
    expect(screen.queryByLabelText(/Automatisch bewerten/)).not.toBeInTheDocument();

    rerender(
      <ScoringRulesEditor
        value={{ max_points: 10 }}
        onChange={vi.fn()}
        challengeType="quiz"
      />,
    );
    expect(screen.getByLabelText(/Automatisch bewerten/)).toBeInTheDocument();
  });

  it('renders quiz options editor for quiz type and adds an option', async () => {
    const onChange = vi.fn();
    render(
      <ScoringRulesEditor
        value={{ max_points: 10, options: ['Alpha'] }}
        onChange={onChange}
        challengeType="quiz"
      />,
    );
    expect(screen.getByDisplayValue('Alpha')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Option hinzufügen/i }));
    expect(onChange).toHaveBeenCalledWith({
      max_points: 10,
      options: ['Alpha', ''],
    });
  });

  it('shows correct_option select only when quiz + auto_approve, populated from options', () => {
    const { rerender } = render(
      <ScoringRulesEditor
        value={{ max_points: 10, auto_approve: false, options: ['Alpha', 'Beta'] }}
        onChange={vi.fn()}
        challengeType="quiz"
      />,
    );
    expect(screen.queryByLabelText(/Richtige Antwort/)).not.toBeInTheDocument();

    rerender(
      <ScoringRulesEditor
        value={{ max_points: 10, auto_approve: true, options: ['Alpha', 'Beta'] }}
        onChange={vi.fn()}
        challengeType="quiz"
      />,
    );
    expect(screen.getByLabelText(/Richtige Antwort/)).toBeInTheDocument();
  });

  it('clears correct_option when auto_approve is unchecked', async () => {
    const onChange = vi.fn();
    render(
      <ScoringRulesEditor
        value={{ max_points: 10, auto_approve: true, correct_option: 'Alpha', options: ['Alpha', 'Beta'] }}
        onChange={onChange}
        challengeType="quiz"
      />,
    );
    await userEvent.click(screen.getByLabelText(/Automatisch bewerten/));
    expect(onChange).toHaveBeenCalledWith({
      max_points: 10,
      auto_approve: false,
      options: ['Alpha', 'Beta'],
    });
  });

  it('shows expected_value input for qr_scan + auto_approve', async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ScoringRulesEditor
        value={{ max_points: 10 }}
        onChange={onChange}
        challengeType="qr_scan"
      />,
    );
    expect(screen.queryByLabelText(/Erwarteter QR-Wert/)).not.toBeInTheDocument();

    rerender(
      <ScoringRulesEditor
        value={{ max_points: 10, auto_approve: true }}
        onChange={onChange}
        challengeType="qr_scan"
      />,
    );
    const input = screen.getByLabelText(/Erwarteter QR-Wert/);
    await userEvent.type(input, 'X');
    expect(onChange).toHaveBeenLastCalledWith({
      max_points: 10,
      auto_approve: true,
      expected_value: 'X',
    });
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = render(
      <ScoringRulesEditor
        value={{ max_points: 100 }}
        onChange={vi.fn()}
        challengeType="photo"
      />,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // With one name for all, a screen reader lists "Option entfernen"
  // three times and none says which option goes.
  it('names each remove button with the number of its option', async () => {
    const onChange = vi.fn();
    render(
      <ScoringRulesEditor
        value={{ max_points: 10, options: ['Alpha', 'Beta'] }}
        onChange={onChange}
        challengeType="quiz"
      />,
    );
    expect(screen.getByRole('button', { name: 'Option entfernen: Option 1' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Option entfernen: Option 2' }));
    expect(onChange).toHaveBeenCalledWith({ max_points: 10, options: ['Alpha'] });
  });
});
