# Onboarding Hero Assets

Hero illustrations of the onboarding wizard, one SVG for each step. Components import the SVGs.

| Hero | Source frame | Used by | SVG bytes |
|---|---|---|---|
| `hero-language` | `language_selection.png` | `WelcomeStep.tsx` | 36,806 |
| `hero-profile` | `profile_setup.png` | `ProfileSetupStep.tsx`, `EntryChoiceStep.tsx` | 12,144 |
| `hero-team-balloon` | `team_selection.png` | `FindTeamStep.tsx` | 14,916 |
| `hero-team-join` | `join_team.png` | `JoinTeamStep.tsx` | 14,916 |
| `hero-team-ferris` | `pick_team_name.png` | `PickTeamNameStep.tsx` | 24,157 |
| `hero-team-pending` | `team_name_pending.png` | `TeamNamePendingStep.tsx` | 24,157 |
| `hero-team-invite` | `invite_team.png` | `InviteTeamStep.tsx` | 14,916 |
| `hero-team-waiting` | `waiting_for_team.png` | `TeamWaitingStep.tsx` | 14,916 |

The folder also holds four small icons. `FindTeamStep.tsx` imports `add_team.svg`, `join_team.svg`
and `check_circle.svg`. `PickTeamNameStep.tsx` and `TeamNamePendingStep.tsx` import `info.svg`.

Total of the images: 165,489 bytes (about 162 KiB), of which the eight hero SVGs are 156,928 bytes
and the four icons are 8,561 bytes. The target is at most 300 KB.

## Provenance

The SVGs are committed as they are. The design mockups are not part of this tree, so a new hero is
added by exporting it from the design tool and placing the file here.

## Size budget

The hero SVGs are 95% of the folder. If the folder passes 300 KB, run `svgo` over the SVGs.
