/**
 * Client-side hint for the password policy: at least 8 characters and at
 * least three of the four character classes (upper case, lower case, digit,
 * special character). Mirrors the backend's `validate_password_policy`, which
 * is the authority. This only spares the user a round trip for the common
 * case.
 */
export function validatePasswordPolicy(password: string): boolean {
  if (password.length < 8) return false;
  let criteria = 0;
  if (/[A-Z]/.test(password)) criteria++;
  if (/[a-z]/.test(password)) criteria++;
  if (/[0-9]/.test(password)) criteria++;
  if (/[!@#$%^&*()_+\-=[\]{}|;:,.<>?]/.test(password)) criteria++;
  return criteria >= 3;
}
