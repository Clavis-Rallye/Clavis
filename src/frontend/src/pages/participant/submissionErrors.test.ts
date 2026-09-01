import { describe, it, expect } from 'vitest';
import { ApiError } from '../../api/client';
import { mapSubmissionError } from './submissionErrors';

// The test setup forces German, so the asserts read the German text the forms
// show, not the API's detail.
describe('mapSubmissionError', () => {
  it('maps TEAM_ALREADY_SUBMITTED to the translated team message and asks for a reload', () => {
    const err = new ApiError(
      422,
      'Dein Team hat diese Challenge bereits beantwortet.',
      'TEAM_ALREADY_SUBMITTED',
    );

    const view = mapSubmissionError(err);

    expect(view.reload).toBe(true);
    // Only this refusal keeps the member's own input for the view.
    expect(view.teammate).toBe(true);
    expect(view.message).toBe(
      'Ein anderes Teammitglied hat diese Aufgabe bereits beantwortet. Die Antwort eures Teams wird angezeigt.',
    );
    // The server's single-language detail never reaches the participant.
    expect(view.message).not.toBe(err.detail);
  });

  it('maps an unknown code to the generic fallback without a reload', () => {
    const view = mapSubmissionError(new ApiError(422, 'Something in English', 'A_FUTURE_CODE'));

    expect(view.reload).toBe(false);
    expect(view.message).toBe(
      'Die Antwort konnte nicht gesendet werden. Bitte versuche es erneut.',
    );
  });

  // An image above the decode cap is refused with 413 IMAGE_TOO_LARGE and nothing
  // is stored. The message tells the team to retake the photo instead of retrying
  // the same file.
  it('maps IMAGE_TOO_LARGE to the translated photo-size message without a reload', () => {
    const err = new ApiError(413, 'Image exceeds the decode limit.', 'IMAGE_TOO_LARGE');

    const view = mapSubmissionError(err);

    expect(view.reload).toBe(false);
    expect(view.message).toBe(
      'Das Foto ist zu groß. Bitte nimm es mit geringerer Auflösung auf.',
    );
    expect(view.message).not.toBe(err.detail);
  });

  // The server answers a file that is no readable picture with 415
  // IMAGE_NOT_DECODABLE. The team takes another photo.
  it('maps IMAGE_NOT_DECODABLE to the translated message without a reload', () => {
    const err = new ApiError(415, 'The file is not a readable image.', 'IMAGE_NOT_DECODABLE');

    const view = mapSubmissionError(err);

    expect(view.reload).toBe(false);
    expect(view.teammate).toBe(false);
    expect(view.message).toBe('Diese Datei ist kein lesbares Bild. Bitte nimm ein anderes Foto auf.');
    expect(view.message).not.toBe(err.detail);
  });

  // The server cleans video and audio and refuses a file it cannot read. The
  // code must win over the rule that an uncoded 422 shows its English detail.
  it('maps MEDIA_NOT_READABLE to the translated message without a reload', () => {
    const err = new ApiError(422, 'The video or audio file cannot be read: no box', 'MEDIA_NOT_READABLE');

    const view = mapSubmissionError(err);

    expect(view.reload).toBe(false);
    expect(view.message).toBe(
      'Diese Datei kann Clavis nicht lesen. Bitte nimm das Video oder die Aufnahme neu auf.',
    );
    expect(view.message).not.toBe(err.detail);
  });

  // An uncoded 422 keeps its detail, because the API client localizes
  // validation details. Any other uncoded answer would be English.
  it('keeps the detail of an uncoded 422 and does not reload', () => {
    const view = mapSubmissionError(new ApiError(422, 'Bitte gib eine Antwort ein.'));

    expect(view).toEqual({ message: 'Bitte gib eine Antwort ein.', reload: false, teammate: false });
  });

  it('translates any other uncoded ApiError instead of showing its English detail', () => {
    const view = mapSubmissionError(new ApiError(503, 'Service unavailable'));

    expect(view.reload).toBe(false);
    expect(view.message).not.toBe('Service unavailable');
  });

  // A run that ended or paused while the form was open answers with a run-state
  // code. The form shows German and the view reloads, which swaps the form for the
  // matching notice.
  it.each([
    ['RUN_ENDED', /beendet/],
    ['RUN_PAUSED', /pausiert/],
    ['RUN_NOT_STARTED', /noch nicht begonnen/],
  ])('maps %s to German and reloads', (code, expected) => {
    const view = mapSubmissionError(
      new ApiError(422, 'Cannot create submission when run is in ended state.', code),
    );

    expect(view.reload).toBe(true);
    expect(view.teammate).toBe(false);
    expect(view.message).toMatch(expected);
  });

  it('stringifies a non-API error and does not reload', () => {
    const view = mapSubmissionError(new Error('boom'));

    expect(view).toEqual({ message: 'Error: boom', reload: false, teammate: false });
  });
});
