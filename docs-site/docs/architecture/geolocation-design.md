---
title: Geolocation from video evidence
sidebar_position: 7
---

# Geolocation from video evidence

Design proposal, October 7, 2026. **The localization engine, telemetry importer
and location-claim schema below are not implemented.** This refines the
[video evidence roadmap](roadmap). Accuracy requires qualification on the intended
hardware and footage.

A GoPro GPS sample describes the recorder's position. The physical traffic camera
visible in a frame is a separate object. Waldo should retain the image evidence
even when it cannot locate that object. Detector confidence, track continuity
and location confidence answer different questions.

## What each case can establish

| Available evidence | Defensible output | Missing constraint or failure case |
| --- | --- | --- |
| Recorder GPS and detection | Recorder trajectory and a source-linked observation | No asset position; copying GPS onto the asset is invalid. |
| Qualified pixel-to-ray calibration | Camera-relative bearing or angular extent | No world heading or metric distance. |
| Calibration plus synchronized Earth-referenced camera pose | World ray or bearing cone from an uncertain origin | A single ray leaves distance unresolved. |
| World ray plus measured metric depth | Estimated 3D point and uncertainty | Depth must refer to the selected feature, with range versus optical-axis depth specified. |
| World ray plus surveyed plane/terrain/3D surface | Conditional intersection estimate | The observed feature must lie on that surface; terrain below an elevated housing does not locate the housing. |
| Matched static feature in several calibrated views with metric poses and parallax | Triangulated estimate | Pure rotation, weak baseline, wrong association or moving features invalidate this method. |
| Surveyed asset or independently checked reference match | Verified claim with measurement/review provenance | A manual map click or high detector score alone is not verification. |

An approximate height, object size or learned monocular depth can be an explicit
prior in an experimental estimate. It must carry its uncertainty and validation
results; it is not interchangeable with measured metric depth. Relative visual
reconstruction without an established metric scale cannot supply world distances.

## Calibrate the image that was actually observed

Define a versioned `CalibrationProfile` for the camera/serial, firmware, lens,
capture resolution, field of view, stabilization/horizon mode, zoom/crop and
valid recording interval. Store the projection model, intrinsics, distortion,
render transform and held-out residuals. Do not reuse a profile after a setting
or mount change without qualification.

GoPro documents several stabilization, zoom and projection modes. Its telemetry
distinguishes capture-relative camera orientation (`CORI`) from image orientation
relative to the body (`IORI`); gravity and magnetometer availability vary by
camera/firmware. Inspect the actual file instead of assuming these streams exist
or provide absolute heading. [GoPro metadata definitions](https://github.com/gopro/gpmf-parser).

Invert the detector's resize, letterbox, tile and crop transforms back to the
decoded source frame. Then apply a qualified source-pixel-to-optical-ray mapping.
For a pinhole image this includes distortion correction; fisheye images require
the matching model. OpenCV provides distinct fisheye calibration and unprojection
operations. [OpenCV fisheye model](https://docs.opencv.org/4.x/db/d58/group__calib3d__fisheye.html).

Do not assume a quaternion reverses all HyperSmooth, horizon-lock, dynamic crop
or nonlinear view warping. Qualify the complete rendered-image mapping per mode,
using available metadata and control images. If it cannot be recovered or
validated, retain observations without a metric world ray. Rolling-shutter
readout, motion blur and ambiguous feature localization must enter the error
budget or cause abstention. The selected feature needs a semantic identity
(housing corner, mounting point, pole base); a bounding-box center is not
automatically any of these.

## Align telemetry, exposure and coordinate frames

`TelemetrySample` preserves raw stream/key, units/scales, sample timing, decoded
values, fix/quality indicators and source-file identity. GPMF sensor clocks and
sample rates need reconciliation with MP4 timing; payload order or nominal FPS
alone is insufficient. Its scaling and axis conventions are explicit metadata.
[GoPro timing documentation](https://github.com/gopro/gpmf-parser/blob/main/docs/README.md),
[GPMF stream definitions](https://github.com/gopro/gpmf-parser/blob/main/GPMF_common.h).

`TimeAlignment` records the mapping from source video PTS/timebase to telemetry
and UTC, its offset/drift, uncertainty, valid intervals and discontinuities.
Keep upload time separate. Chapter joins, trims, proxies, duplicated/skipped
frames and telemetry gaps need explicit mappings. Resolve pose at exposure time;
interpolate only over qualified gaps, using rotation-aware interpolation. Account
for exposure/readout timing during fast motion; do not silently extrapolate.

`PoseEstimate` records position, orientation, frame conventions, quality and
the alignment/calibration revisions used. Validate quaternion order, handedness,
axis direction and transform direction against control observations. Gravity
alone leaves yaw unresolved. Capture-relative orientation needs a surveyed or
otherwise qualified Earth-frame alignment. Course of travel must not become
optical heading for a side-facing, reversed or movable camera.

Represent measured camera-to-body/IMU rotation and receiver-to-optical-center
lever arm as extrinsics with validity intervals. A rigid vehicle mount and a
moving helmet/gimbal need different contracts. An externally measured vehicle
heading helps only when its relationship to the optical camera is known.

Use a declared metric local frame for geometry, then convert to the declared
geographic CRS. Preserve altitude datum and uncertainty; do not combine unknown
altitude with a terrain model as if their heights share a reference. GeographicLib
documents geodetic/local Cartesian conversion and ellipsoidal height.
[GeographicLib coordinate conversion](https://geographiclib.sourceforge.io/2009-03/classGeographicLib_1_1LocalCartesian.html).

## Solve the appropriate geometry

After validated image unprojection, let `d_c` be a unit optical ray, `C` the
optical center in the metric world frame, and `R_wc` the rotation from camera to
world. The candidate feature lies on:

```text
d = R_wc d_c
X(lambda) = C + lambda d, lambda > 0
```

This is a ray, not a point. For a plane `n · X + b = 0`, substitution gives
`lambda = -(n · C + b) / (n · d)`. Reject nearly parallel rays, intersections
behind the camera and a plane assumption incompatible with the feature. A known
pole base may use a ground constraint; its elevated camera requires separate
geometry or an explicit measured offset. A terrain model supplies terrain
elevation, not the height of every object above it. Projection and pose must use
consistent conventions. [OpenCV projection and pose reference](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).

A measured range `rho` gives `X = C + rho d`. Optical-axis depth `z` instead
requires `rho = z / d_c,z`; reject an incompatible sign or degenerate ray. Never
mix these two depth conventions. A calibrated stereo or depth sensor also needs
its transform and time alignment to the image camera.

For fixed cameras, triangulate corresponding physical features across translated
views using qualified projection matrices. OpenCV's multi-view triangulation
API takes corresponding image points and projection matrices.
[OpenCV triangulation](https://docs.opencv.org/4.x/d0/dbd/group__triangulation.html).

The proposed solver must check correspondence quality, parallax, metric pose
scale, positive depth, reprojection residuals and stability under input errors.
More frames cannot cure a shared pose bias or a wrong asset association. Keep
competing associations rather than forcing a merge. A moving object requires
synchronized stereo or an explicit time-dependent motion model; applying static
triangulation across its successive positions is not valid.

## Preserve uncertainty and query it honestly

Propagate GPS, pose, timing, pixel/feature, calibration and depth/surface errors
through each method, using a documented Jacobian or Monte Carlo model. Include
correlated errors and bias assumptions. Detector scores do not define positional
covariance. DOP describes satellite geometry; it is not a radius in meters.
[NovAtel explanation of DOP](https://novatel.com/an-introduction-to-gnss/basic-concepts/computation).

As first-order sensitivity checks, position timing error grows roughly as speed
times time offset, and transverse angular error as range times angular error
in radians. These checks do not replace the complete uncertainty model. Do not
average correlated GPS or heading errors away by treating every frame as an
independent measurement. If a probability model is unqualified, show a stated
bound/scenario range rather than labeling it a calibrated confidence interval.

Proposed `LocationClaim` fields:

- Workspace, asset hypothesis and supporting observation IDs; immutable revision
  and superseded-claim link.
- `unknown`, `estimated` or `verified` state; point/region/ray geometry and CRS,
  or an explicit unavailable reason.
- Method, calibration/alignment/pose/depth/surface references, assumptions and
  validation diagnostics; uncertainty representation and its interpretation.
- Reference measurement and reviewer provenance for verification. Verification
  does not mean zero error or establish every track as the same physical asset.

Location revisions must not overwrite source evidence or existing training
artifacts. Resolve all inputs in the authenticated workspace; an LLM proposes a
typed query, while server code applies permission and location-policy checks.

Area search should return located matches, possible matches whose qualified
uncertainty region overlaps the area, and a separate unlocated group by default.
Expose an explicit strict-location option with excluded/unlocated counts. An
unbounded ray is not a finite location region unless a justified distance bound
exists. A disjoint recorder path or a point estimate outside the area cannot
alone exclude an asset: the recorder can see across the boundary. Label whether
time filtering concerns recording/capture time, observation PTS or an asset's
known existence interval; a timestamped sighting does not prove continued presence.

## Staged contracts and acceptance gates

These are proposed tests, not passing implementation results. Choose tolerances
from the intended hardware, use case and independently surveyed held-out data.

| Stage | Implementable contract | Required acceptance evidence |
| --- | --- | --- |
| 1. Recorder evidence | Original MP4/GPMF inventory, trajectory and source-time mapping; no asset coordinates | Golden files with missing GPS, poor fix, chapters and gaps decode reproducibly. Known time events expose offset/drift. Missing metadata remains unknown. |
| 2. Calibrated bearings | Mode-qualified source-pixel mapping and camera-relative rays; world rays only with qualified Earth pose | Held-out targets across image edges, camera rotations and crop/stabilization conditions. Wrong profile, unresolved yaw or stale mount calibration prevents world-ray output. |
| 3. Conditional positions | Independent depth, surface and static triangulation methods with declared assumptions | Surveyed targets at several heights/ranges. Elevated housing rejects a ground shortcut; pure rotation/weak parallax, moving features and incorrect matches abstain. Injected clock/pose/GPS errors change uncertainty or invalidate output. |
| 4. Reviewed assets and search | Versioned location claims, reversible associations and typed area/time policy | Independent passes recover measured positions within empirically qualified uncertainty; shared bias is retained. Boundary, unbounded-ray and unlocated fixtures never silently become outside-area results. Foreign-workspace evidence is inaccessible. |

Measure horizontal/vertical error, uncertainty coverage, abstention rate and
false spatial exclusions by capture mode and geometry. Also report observation
recall separately: good localization of a few detections does not establish that
all visible cameras were found. Fix acceptance thresholds before a held-out test;
do not invent an accuracy promise from metadata labels.

## Practical GoPro test material

Start with original, unedited MP4s and all related chapters, the camera model and
firmware, capture settings, mount description and GPS availability. Preserve the
originals even when analysis uses proxies. Inspect which telemetry actually exists
before selecting a localization method.

Record a calibration target throughout the field of view in the intended mode;
include controlled rotations and translated views of surveyed stationary targets
at different heights and distances. Use paired stabilization/horizon/crop modes
where comparison is needed. Measure the mounting transform and optical-center
offset to any external pose sensor. Include a visible event with an independently
recorded time reference to validate alignment.

A useful first road fixture includes repeat passes with meaningful lateral
parallax, elevated camera housings, poles/lights as hard negatives, occlusion,
moving objects and poor-GPS segments. Ground-truth asset coordinates and heights
must have their own measurement uncertainty. Footage alone can test detection
and tracking; trustworthy world-location evaluation needs these independent
controls. No new collection or engine implementation is implied by this proposal.
