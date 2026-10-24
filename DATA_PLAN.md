# Submission data plan: synthetic matches built from real movement

## Why

The official rules (section 4.7) require synthetic, football-realistic data. Our first simulator produced plausible events, but player movement did not look like football. Real tracking data looks right, but a real match is not synthetic. The plan is to generate new, fictional matches whose player and ball movement is taken from real open tracking data.

## Source

DFL open data: Bassek et al., *An integrated dataset of spatiotemporal and event data in elite soccer*, Scientific Data (2025), [figshare](https://springernature.figshare.com/articles/dataset/An_integrated_dataset_of_spatiotemporal_and_event_data_in_elite_soccer/28196177), CC BY 4.0. Seven matches (2 Bundesliga, 5 2. Bundesliga), each with official events and 25 Hz positions for all players and the ball. Attribution goes in the README and on the page.

## Method

1. **Cut each source match into play sequences** at dead-ball moments (throw-ins, goal kicks, free kicks, corners, kick-offs). Each sequence keeps its own events and positions, so actions and movement stay consistent.
2. **Assign roles.** In every sequence, one side becomes fictional club A and the other club B. Coordinates are mirrored where needed so A always attacks the same way within a half.
3. **Stitch a new match.** A seeded generator picks sequences from all seven matches and orders them into a new timeline. The seed also controls the storyline: how many goal sequences are included, for which side, and when. Seams fall only at dead-ball moments, and positions are blended over about 2 seconds so players do not jump.
4. **Vary.** Optional left-right mirroring and a small time stretch (±5%) make reused sequences look different.
5. **Fictional identity.** Teams, players, shirt numbers and colours are fictional and assigned per seed. No real names reach the output.
6. **Run the existing pipeline unchanged.** The stitched match uses the same format as `insidegame/dfl.py`, so the analyst, play-by-play, agent commentary, stats layer and studio analysis all work on it as they do now.

## What this gives the submission

- Movement that looks like real football, which was the main weakness of the first simulator.
- New matches on demand, with scripted storylines for the demo, from open data with clear attribution.
- A defensible answer to "is it synthetic?": the matches never happened, the participants are fictional, and the generator is part of the project ("data innovation" in the challenge brief).

## Risks

- Judges may see stitched real movement as less synthetic than a pure simulation. The README will explain the method plainly.
- Seams can show small discontinuities in team shape. Cutting at dead balls and blending positions keeps these short.
- Statistics from a stitched match (possession, field tilt, distance) are properties of the new match, not of any real one.

## Effort

About half a day: sequence cutting and stitching (new module), fictional identity, a check that seams look natural, then a rebuild of the demo bundles. The real Köln–Bayern runs stay local as development and testing data and are not part of the submission.
