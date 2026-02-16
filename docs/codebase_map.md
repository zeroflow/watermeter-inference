# Codebase Map

> Auto-generated reference. Line numbers as of 2026-02-15.

## Python Package: `watermeter/`

### `app.py` (180 lines) -- FastAPI app, lifespan, router wiring

- `safe_subpath(base, user_path)` L22 -- path traversal guard
- `_background_tasks: set` L31, `_create_background_task(coro)` L34
- `lifespan(app)` L42-85 -- startup/shutdown: service, inference, MQTT, cyclic
- `tags_metadata` L89-126, `app = FastAPI(...)` L129
- `main()` L162-179 -- uvicorn entry
- Routers included L131-158: pages, service, config, roi, label, training, models

### `routes/pages.py` (99 lines) -- HTML page routes

- `GET /` L24, `GET /label` L36, `GET /roi-config` L48, `GET /config-editor` L60, `GET /training` L72
- `GET /api/status/html` L84 -- HTMX status fragment

### `routes/service.py` (223 lines) -- Core service API

- Pydantic: `TrainingSubmission` L33, `SetValueRequest` L42
- `GET /api/status` L54, `POST /api/trigger` L68, `POST /api/reset` L87
- `POST /api/set-value` L100, `POST /api/toggle-ha-publish` L134
- `POST /api/submit-training` L148, `GET /api/confirmation/status` L196, `GET /health` L214

### `routes/config.py` (93 lines) -- Config management

- `GET /api/config` L30, `POST /api/config/save` L53, `GET /api/config/schema.json` L90

### `routes/roi.py` (716 lines) -- ROI configuration wizard

- Pydantic: `RotationSubmission` L24, `MarkerBox` L28, `MarkersSubmission` L35, `DigitRoi` L39, `DigitsSubmission` L46, `SingleRoiSubmission` L50, `AnalogRoi` L58, `AnalogsSubmission` L65
- `_load_rotated_reference()` L73
- `POST /api/roi/fetch-image` L104, `GET /api/roi/reference-image` L144, `GET /api/roi/config` L162
- `POST /api/roi/rotation` L182, `DELETE /api/roi/rotation` L213
- `POST /api/roi/markers` L244, `DELETE /api/roi/markers` L318, `GET /api/roi/marker-image/{id}` L358
- `POST /api/roi/digits` L374, `DELETE /api/roi/digits` L442, `GET /api/roi/digit-image/{id}` L480
- `POST /api/roi/digit-preview` L496
- `POST /api/roi/analogs` L550, `DELETE /api/roi/analogs` L616, `GET /api/roi/analog-image/{id}` L654
- `POST /api/roi/analog-preview` L670

### `routes/label.py` (213 lines) -- Labeling interface API

- Pydantic: `LabelSubmission` L21, `DeleteSubmission` L27
- `GET /api/label/next-image` L38, `POST /api/label/submit` L98, `POST /api/label/delete` L183

### `routes/training.py` (216 lines) -- Training job management

- Pydantic: `TrainingConfig` L17 (field_validator for seeds)
- `GET /api/training/status` L45, `POST /api/training/start` L68, `POST /api/training/cancel` L95
- `DELETE /api/training/queue/{index}` L124, `DELETE /api/training/queue` L143
- `POST /api/benchmark/cancel` L159, `GET /api/training/logs/{job_id}` L183, `GET /api/training/progress/{job_id}` L202

### `routes/models.py` (706 lines) -- Model management + training data tools

- `GET /api/models/architectures` L30, `GET /api/models` L44, `GET /api/models/{type}/{id}` L81
- `POST /api/models/{type}/{id}/activate` L103, `POST /api/models/{type}/{id}/archive` L142
- `DELETE /api/models/{type}/{id}` L164, `GET /api/models/{type}/{id}/logs` L186
- `POST /api/models/{type}/{id}/benchmark` L210, `GET /api/training-data/stats` L231
- `POST /api/training-data/dedup` L277
- `_prune_previews` L305, `POST /api/training-data/prune/preview` L314, `POST /api/training-data/prune/confirm` L370
- `_mislabel_scans` L418, `_make_thumbnail_base64(path)` L421
- `scan_mislabeled(model_type, gt_base, ...)` L434, `confirm_mislabeled(model_type, actions)` L517
- `POST /api/training-data/mislabel/scan` L587, `POST /api/training-data/mislabel/confirm` L637

### `watermeter_service.py` (1939 lines) -- Main service orchestration

**class `WatermeterService`** L30:
- `__init__(config_path)` L33
- Image fetching: `fetch_images()` L120, `fetch_whole_image()` L160, `process_whole_image(img_bytes)` L182
- Marker alignment: `SEARCH_MARGIN` L246, `CONFIDENCE_THRESHOLD` L247, `_load_marker_templates()` L249, `invalidate_marker_cache()` L278, `_align_with_markers(img)` L282, `_extract_roi(img, roi_cfg)` L367
- Inference: `run_inference(images)` L397, `calculate_total(predictions)` L468
- Validation: `check_consistency(predictions)` L533, `validate_plausibility(total, ...)` L595
- Rate/leak: `_add_to_rate_history(rate)` L660, `_calculate_average_rate_per_hour()` L667, `_check_sustained_consumption()` L684
- BL-07 Confirmation: `_get_confirmation_config()` L728, `_should_request_confirmation()` L742, `_publish_confirmation_request()` L783, `_cancel_confirmation_timer()` L836, `_confirmation_timeout()` L842, `_do_confirmation_timeout()` L849, `_handle_confirmation_response(payload)` L878, `get_confirmation_status()` L969
- BL-04 Correction: `_get_ordered_position_ids()` L994, `_estimate_expected_range()` L1008, `_recalculate_with_replacement()` L1027, `_check_consistency_improvement()` L1067, `_check_cross_arrow_consistency()` L1118, `correct_predictions(predictions, ...)` L1172
- Pipeline: `save_low_confidence(predictions, images)` L1282, `process_reading()` L1371, `publish_to_mqtt(status)` L1615
- Manual: `reset_previous_value()` L1652, `set_manual_value(value)` L1677, `toggle_ha_publish()` L1753
- HA/MQTT: `publish_discovery()` L1759, `on_mqtt_connect()` L1798, `on_mqtt_message()` L1828, `start_mqtt()` L1864, `stop_mqtt()` L1895
- Cyclic: `_cyclic_loop()` L1902, `start_cyclic_loop()` L1913, `stop_cyclic_loop()` L1921

Singleton: `get_service()` L1933

### `training_manager.py` (1314 lines) -- Training/benchmark orchestration

- `JobStatus` (Enum) L19, `TrainingJob` (dataclass) L29, `BenchmarkJob` (dataclass) L76

**class `TrainingManager`** L124:
- `__init__(models_base, training_base, config_path)` L127
- Queue: `start_training(config)` L138, `_start_training_now(config)` L155, `start_benchmark(model_type, model_id)` L170
- Control: `cancel_training(job_id, clear_queue)` L203, `cancel_benchmark(job_id)` L219
- Status: `get_training_status()` L236, `get_benchmark_status()` L242, `get_queue()` L248, `remove_from_queue(index)` L253, `clear_queue()` L261, `get_job_logs(job_id)` L268
- Execution: `_run_training(config)` L275, `_process_next_in_queue()` L375, `_run_auto_benchmarks(model_type, model_id)` L394
- Core: `_execute_training(job, config)` L420 (~400 lines), `_persist_training_logs(job, model_dir)` L832, `_persist_failure_metadata(job, config)` L862, `_get_failure_dir(config)` L897
- Arrows: `_create_arrow_dataset(gt_dir, transform, step_size)` L907
- Benchmark: `_run_benchmark(model_type, model_id)` L951, `_execute_benchmark(job, model_type, model_id)` L1034
- Helpers: `_generate_arrow_classes()` L1246, `_round_to_arrow_class(value)` L1259, `_collect_benchmark_images(gt_dir)` L1286

Singleton: `get_training_manager()` L1308

### `model_manager.py` (341 lines) -- Model metadata & files

**class `ModelManager`** L15:
- `__init__(base_path)` L18, `_validate_model_id(id)` L28, `_get_model_types_dir(type)` L34
- `list_models(type)` L40, `get_model(type, id)` L76, `save_metadata(type, id, metadata)` L113
- `delete_model(type, id)` L140, `get_active_model(type)` L166, `activate_model(type, id)` L193
- `get_model_path(type, id)` L249, `create_model_id(type, arch, res)` L268
- `archive_model(type, id)` L289, `get_model_metadata(type, id)` L308, `refresh()` L321

Singleton: `get_model_manager()` L335

### `inference.py` (525 lines) -- OpenVINO inference with hot-reload

- **class `Classifier`** L19: `__init__` L20, `preprocess` L29, `predict` L39 -> (label, conf), `predict_detailed` L48
- **class `Regressor`** L61: `__init__` L68, `preprocess` L76, `predict` L87 -> (value, conf), `predict_detailed` L115
- `_detect_training_mode()` L125
- **class `InferenceService`** L155: `__init__` L158, `initialize(config_path)` L164, `reload_models()` L214, `predict(model_type, image_bytes)` L281, `predict_detailed` L300, `get_classifier` L310, `is_reloading` L320
- `validate_model_config(config_path)` L335
- `get_inference_service()` L410 (singleton)
- Legacy Label Studio: `setup()` L446, `predict_ls(tasks, ...)` L455

### `config_utils.py` (490 lines) -- YAML config with comment preservation

- `_yaml` (global) L20, `get_yaml()` L26
- `load_config(path)` L31, `save_config(config, path)` L46
- `load_config_string(yaml_string)` L60, `dump_config_string(config)` L73
- `update_config(path, updater)` L88, `validate_config(yaml_string)` L108
- `CONFIG_SCHEMA` L135-479, `get_config_schema()` L482, `get_config_schema_json()` L487

### `image_hash.py` (506 lines) -- Perceptual hashing & dedup

- `compute_dhash(image_bytes, hash_size)` L15, `hamming_distance(h1, h2)` L50
- **class `HashCache`** L64: `__init__` L67, `_load` L74, `_save` L88, `add` L96, `get_all_hashes` L101, `get_hashes_dict` L105, `remove` L109, `find_near_duplicate(new_hash, threshold)` L114, `scan_and_update` L131
- `purge_duplicates(input_dir, threshold, gt_dirs)` L147
- `cluster_images_by_hash(hashes, threshold)` L234, `select_prune_candidates(clusters, hashes)` L285
- `compute_prune_preview(gt_base, threshold)` L328, `confirm_prune(gt_base, preview)` L432

### `training_core.py` (295 lines) -- Shared training utilities

- `IMAGENET_MEAN` L49, `IMAGENET_STD` L50
- `set_all_seeds(seed)` L28, `worker_init_fn(worker_id)` L39
- `create_transforms(resolution)` L53, `stratified_split(dataset, train_ratio)` L82
- `compute_class_weights(dataset, indices, device)` L109
- `export_to_openvino(model, resolution, output_dir, filename)` L130
- `preprocess_image(image_path, resolution)` L172
- **class `RegressionArrowDataset`** L190: `__init__` L199, `__len__` L219, `__getitem__` L222
- `stratified_split_regression(dataset, train_ratio)` L232
- `regression_predict(raw_output)` L265
- `circular_error(pred, true, period=10.0)` — L281: Shortest-path error on circular dial scale
- `softmax_predict(logits, classes)` L299

### `persistence.py` (74 lines) -- JSON state persistence

- **class `StateStore`** L16: `__init__` L19, `save(data)` L23, `load()` L45, `clear()` L66

### `__main__.py` (4 lines) -- Entry point, calls `app.main()`

---

## Templates: `watermeter/templates/`

| Template | Lines | Purpose | Key HTMX / JS |
|----------|-------|---------|----------------|
| `dashboard.html` | 59 | Main dashboard | `hx-post="/api/trigger"` L18, `hx-get="/api/status/html" hx-trigger="load, every 5s"` L49 |
| `_nav.html` | 123 | Shared nav (mobile tabs + desktop rail) | Links: /, /label, /training, /roi-config, /config-editor |
| `status_fragment.html` | 112 | HTMX fragment: value, rejected, warnings, images | Rendered server-side |
| `label.html` | 450 | Labeling + keyboard shortcuts | `loadNextImage()`, `submitLabel()`, `deleteImage()` |
| `roi_config.html` | 238 | 4-step ROI wizard (canvas overlay) | Inline script loads `roi-config.js` |
| `config_editor.html` | 228 | Monaco YAML editor | Loads `config-editor.js`, Monaco from CDN |
| `training.html` | 1285 | Training: stats, form, queue, progress, models, data tools | Loads `training.js` |

---

## Static JS: `watermeter/static/`

### `dashboard.js` (132 lines)
`toggleAutoRefresh` L1, `toggleHaPublish` L16, `hideStatusMessage` L33, `showStatusMessage` L42, `setMeterValue` L51, `submitForTraining` L83

### `label.js` (257 lines)
`loadNextImage` L4, `updateProgress` L86, `submitLabel` L108, `showMessage` L143, `deleteImage` L156, `validateAndSubmit` L188

### `training.js` (1017 lines)
- State: `currentJobId`, `allModels`, `sortColumn`, `sortAsc` L8-16
- `CURATED_MODELS` L57-83, `initArchCombobox()` L89
- `loadTrainingStats()` L195, `pollTrainingStatus()` L302 (2s interval)
- `updateBenchmarkProgress` L412, `loadBenchmarkLogs` L430, `startBenchmark` L455, `cancelBenchmark` L477
- `updateProgress` L502, `formatDuration` L547, `hideLogSectionIfIdle` L558, `loadLogs` L568
- `startTraining` L594, `cancelTraining` L656, `renderQueue` L692, `removeFromQueue` L726, `clearQueue` L741
- `loadModels` L755, `filterModels` L779, `sortModels` L791, `renderModels` L804
- `activateModel` L942, `viewModelLog` L964, `closeLogModal` L988, `deleteModel` L994

### `roi-config.js` (1490 lines, inline in template)
- Canvas/overlay state L1-44, mouse events L62-196
- Drawing: `drawCrosshair` L200, `drawBox` L225, `render` L266
- Markers: `selectMarkerInPicture` L384, `saveMarkers` L449, `restartMarkers` L488, `changeMarkers` L502
- Digits: `updateDigitBoxes` L564, `selectDigitInPicture` L627, `runDigitInference` L703, `saveDigits` L751, `changeDigits` L810
- Completed steps: `updateCompletedSteps` L858
- Analogs: `updateAnalogBoxes` L961, `selectAnalogInPicture` L1025, `runAnalogInference` L1101, `saveAnalogs` L1149, `changeAnalogs` L1208
- Rotation: `getTotalRotation` L1262, `saveRotation` L1299, `changeRotation` L1346
- Image loading: `fetchAndReload` L1366, `loadImage` L1392, `loadConfig` L1418

### `config-editor.js` (167 lines)
`initEditor` L17, `updateStatus` L58, `loadConfig` L73, `saveConfig` L98, `showMessage` L150

---

## Static CSS: `watermeter/static/style.css` (1350 lines)

**Variables (L8-18):** `--primary: #2563eb`, `--primary-light: #3b82f6`, `--primary-dark: #1d4ed8`, `--success: #059669`, `--warning: #d97706`, `--danger: #dc2626`, `--bg-dark: #1e293b`, `--bg-light: #f8fafc`, `--text-dark: #0f172a`, `--text-light: #64748b`, `--border: #e2e8f0`

**Sections:** Reset L1-26, Header L39-61, Buttons L63-96, Toggle L98-137, Status msgs L139-151, Main L153-163, Total value L165-231, Warnings L232-258, Images grid L260-398, Responsive L400-469, Error L471-486, Empty L487-502, ROI layout L504-628, Markers L630-739, Digit ROI L741-907, Completed steps L952-1060, Analog ROI L1062-1244, Rejected (BL-14) L1246-1301, Manual input (BL-15) L1303-1350

---

## Config Structure (`config.yaml`)

```
aiote:          # Device (host, image_path, timeout, fetch_delay)
images:         # Sources (process_separate, src, digits[], arrows[])
detection:      # ROI (rotation, digits{count,rois}, analogs{count,rois}, markers[])
trigger:        # Mode (mqtt|cyclic|both, cyclic_interval)
mqtt:           # Broker (broker, port, client_id, topics)
homeassistant:  # HA (enabled, discovery_prefix, device, sensor)
inference:      # Models (confidence_threshold, device, models, classes, resolution)
plausibility:   # Checks (reverse, rate_limit, consistency, leak_detection)
correction:     # BL-04 (confidence_threshold, signals, top_k)
confirmation:   # BL-07 (timeout, min_warnings, rate_jump)
low_confidence: # Training capture (save_enabled, dedup, threshold)
persistence:    # State (enabled, state_file)
dashboard:      # Web UI (host, port, auto_refresh_interval)
logging:        # Logging (level, format, file)
```

---

## Root-Level Scripts (standalone, not in package)

`train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py`
