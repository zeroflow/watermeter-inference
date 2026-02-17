# Codebase Map

> Auto-generated reference. Line numbers as of 2026-02-17.

## Python Package: `watermeter/`

### `app.py` (200 lines) -- FastAPI app, lifespan, router wiring

- `safe_subpath(base, user_path)` L22 -- path traversal guard
- `_background_tasks: set` L31, `_create_background_task(coro)` L34
- `lifespan(app)` L43-101 -- startup/shutdown: service, inference, MQTT, cyclic, dedup on start
- `tags_metadata` L105-142, `app = FastAPI(...)` L145
- `main()` L182 -- uvicorn entry
- Routers included L161-179: pages, service, config, roi, label, training, models, synthetic, mqtt

### `routes/pages.py` (116 lines) -- HTML page routes

- `GET /` L18, `GET /label` L40, `GET /roi-config` L52, `GET /config-editor` L71, `GET /training` L83
- `GET /api/status/html` L95 -- HTMX status fragment

### `routes/service.py` (229 lines) -- Core service API

- Pydantic: `TrainingSubmission` L34, `SetValueRequest` L43
- `GET /api/status` L49, `POST /api/trigger` L63, `POST /api/reset` L88
- `POST /api/set-value` L101, `POST /api/toggle-ha-publish` L135
- `POST /api/submit-training` L149, `GET /api/confirmation/status` L197, `GET /health` L215

### `routes/config.py` (101 lines) -- Config management

- `GET /api/config` L25, `POST /api/config/save` L48, `GET /api/config/schema.json` L93

### `routes/roi.py` (791 lines) -- ROI configuration wizard

- Pydantic: `RotationSubmission` L25, `MarkerBox` L29, `MarkersSubmission` L36, `DigitRoi` L40, `DigitsSubmission` L47, `SingleRoiSubmission` L52, `AnalogRoi` L59, `AnalogsSubmission` L66
- `_load_rotated_reference()` L74
- `POST /api/roi/fetch-image` L99, `POST /api/roi/image-source` L139 (set image source URL), `GET /api/roi/reference-image` L202, `GET /api/roi/config` L220
- `POST /api/roi/rotation` L240, `DELETE /api/roi/rotation` L271
- `POST /api/roi/markers` L302, `DELETE /api/roi/markers` L376, `GET /api/roi/marker-image/{id}` L416
- `POST /api/roi/digits` L432, `DELETE /api/roi/digits` L500, `GET /api/roi/digit-image/{id}` L538
- `POST /api/roi/digit-preview` L554
- `POST /api/roi/analogs` L614, `DELETE /api/roi/analogs` L680, `GET /api/roi/analog-image/{id}` L718
- `POST /api/roi/analog-preview` L734

### `routes/mqtt.py` (207 lines) -- MQTT configuration routes

- `_has_unresolved_tokens(value)` L31 -- checks for unresolved ${...} tokens
- `GET /api/mqtt/config` L36, `POST /api/mqtt/config` L71, `POST /api/mqtt/test` L124
- Imports: `config_utils`, `watermeter_service`; references `mqtt_client_class`, `_mqtt_callback_api` L18-28

### `routes/label.py` (218 lines) -- Labeling interface API

- Pydantic: `LabelSubmission` L21, `DeleteSubmission` L27
- `GET /api/label/next-image` L32, `POST /api/label/submit` L92, `POST /api/label/delete` L183

### `routes/training.py` (411 lines) -- Training job management + ZIP upload

- Pydantic: `TrainingConfig` L29 (field_validator for seeds, training_mode, learning_rate)
- `GET /api/training/status` L62, `POST /api/training/start` L85, `POST /api/training/cancel` L112
- `DELETE /api/training/queue/{index}` L141, `DELETE /api/training/queue` L160
- `POST /api/benchmark/cancel` L176, `GET /api/training/logs/{job_id}` L200, `GET /api/training/progress/{job_id}` L219
- `_detect_and_sort(images, data_type)` L244 -- detect ZIP format (subdirectory or prefix-based)
- `POST /api/training-data/upload` L309 -- upload ZIP of training images; supports subdirectory and prefix formats

### `routes/models.py` (706 lines) -- Model management + training data tools

- `GET /api/models/architectures` L25, `GET /api/models` L39, `GET /api/models/{type}/{id}` L76
- `POST /api/models/{type}/{id}/activate` L98, `POST /api/models/{type}/{id}/archive` L137
- `DELETE /api/models/{type}/{id}` L159, `GET /api/models/{type}/{id}/logs` L181
- `POST /api/models/{type}/{id}/benchmark` L205, `GET /api/training-data/stats` L226
- `POST /api/training-data/dedup` L272
- `POST /api/training-data/prune/preview` L309, `POST /api/training-data/prune/confirm` L365
- `_make_thumbnail_base64(path)` L422
- `scan_mislabeled(model_type, training_path)` L435, `confirm_mislabeled(model_type, training_path, selected_paths)` L518
- `POST /api/training-data/mislabel/scan` L582, `POST /api/training-data/mislabel/confirm` L632

### `routes/synthetic.py` (157 lines) -- Synthetic data generation API

- Pydantic: `SyntheticConfig` L30 (field_validator for type, count_per_class)
- Module state: `_generation_lock` L19, `_generation_status` L20
- Helper: `_run_generation(config, job_id)` L50 -- background thread target
- `POST /api/synthetic/generate` L89, `GET /api/synthetic/status` L129, `DELETE /api/synthetic/{type}` L140

### `synthetic_generator.py` (507 lines) -- Synthetic training data generator

- Constants: `DIGIT_CLASSES` L19, `ARROW_CLASSES` L20
- Helpers: `_deterministic_seed(cls, i)` L23, `_load_font(size)` L29

**class `DigitRenderer`** L36:
- `__init__()` L48, `render(digit_class, seed)` L51 -- renders digit image from font

**class `DigitCompositor`** L89:
- `__init__(background_dir)` L110, `render(digit_class, seed)` L128 -- composites digit onto photo background

**class `ArrowRenderer`** L174:
- `__init__()` L177, `render(arrow_class, seed)` L180 -- renders 100x100px dial image with ticks and red pointer

**class `TransformPipeline`** L248:
- `__init__(seed)` L251, `apply(img, mode)` L255 -- applies geometric (offset, perspective, fisheye), color (brightness, contrast, cast, shadow, vignette), and noise (gaussian, blur, JPEG artifacts) transforms

**class `_MultiCompositor`** L379:
- `__init__(compositors, seed)` L382, `render(arrow_class, seed)` L386

**class `SyntheticGenerator`** L391:
- `__init__(base_dir)` L394
- `_get_digit_renderer()` L397, `_get_arrow_renderer()` L406
- `generate(type, count_per_class, seed, progress_callback)` L435 -- orchestrates rendering + transforms, writes to ground_truth/
- `delete_synthetic(type)` L490 -- removes all synth_* files from ground_truth

### `photo_master.py` (143 lines) -- Photo-based master image for arrow compositing

- `extract_pointer_mask(img)` L18 -- HSV color thresholding to isolate red pointer, returns binary mask
- `inpaint_background(img, mask)` L45 -- removes pointer with inpainting, returns clean background
- `extract_pointer_template(img, mask)` L62 -- crops pointer region to template

**class `ArrowCompositor`** L75:
- Composites a real pointer template onto inpainted backgrounds at any angle

- `build_arrow_compositor(photos_dir, ...)` L117 -- factory: loads photo set, extracts pointers, returns ArrowCompositor

### `watermeter_service.py` (1912 lines) -- Main service orchestration

**class `WatermeterService`** L243:
- `__init__(config_path)` L245 (implicit via get_service())
- Delegates: `_image_pipeline` (ImagePipeline), `_low_confidence` (LowConfidenceCapture), `_scheduling` (SchedulingManager), `_rate_tracker` (RateTracker), `_leak_detector` (LeakDetector), `_plausibility_checker` (PlausibilityChecker)
- Image fetching (delegates to ImagePipeline): `fetch_images()` L405, `fetch_whole_image()` L410, `process_whole_image(img_bytes)` L415, `invalidate_marker_cache()` L420
- Inference: `run_inference(images)` L424, `calculate_total(predictions)` L495
- Validation: `check_consistency(predictions)` L548 (delegates to PlausibilityChecker), `validate_plausibility(total, ...)` L552 (delegates to PlausibilityChecker)
- Rate/leak: `_check_sustained_consumption()` L560 (delegates to LeakDetector)
- BL-07 Confirmation: `_get_confirmation_config()` L566, `_should_request_confirmation()` L580, `_publish_confirmation_request()` L621, `_cancel_confirmation_timer()` L674, `_confirmation_timeout()` L680, `_do_confirmation_timeout()` L687, `_handle_confirmation_response(payload)` L728, `get_confirmation_status()` L832
- BL-04 Correction: `_get_ordered_position_ids()` L857, `_estimate_expected_range()` L862, `_recalculate_with_replacement()` L881, `_check_consistency_improvement()` L912, `_check_cross_arrow_consistency()` L963, `correct_predictions(predictions, ...)` L1017
- Low confidence: `save_low_confidence(predictions, images)` L1127 (delegates to LowConfidenceCapture)
- Pipeline: `process_reading()` L1133, `publish_to_mqtt(status)` L1418
- Manual: `reset_previous_value()` L1491, `set_manual_value(value)` L1516, `toggle_ha_publish()` L1585
- HA/MQTT: `publish_discovery()` L1648, `on_mqtt_connect()` L1711, `on_mqtt_disconnect(...)` L1742, `on_mqtt_message()` L1749, `start_mqtt()` L1785, `reload_config(new_config)` L1828, `stop_mqtt()` L1883
- Cyclic (delegates to SchedulingManager): `start_cyclic_loop()`, `stop_cyclic_loop()`, `start_stats_loop()`, `stop_stats_loop()`

Singleton: `get_service()` L1907

### `training_manager.py` (1367 lines) -- Training/benchmark orchestration

- `JobStatus` (Enum) L20, `TrainingJob` (dataclass) L30, `BenchmarkJob` (dataclass) L77

**class `TrainingManager`** L125:
- `__init__()` L128
- Queue: `start_training(config)` L139, `_start_training_now(config)` L156, `start_benchmark(model_type, model_id)` L171
- Control: `cancel_training(job_id, clear_queue)` L204, `cancel_benchmark(job_id)` L220
- Status: `get_training_status()` L237, `get_benchmark_status()` L243, `get_queue()` L249, `remove_from_queue(index)` L254, `clear_queue()` L262, `get_job_logs(job_id)` L269
- Execution: `_run_training(config)` L276, `_process_next_in_queue()` L396, `_run_auto_benchmarks()` L415
- Core: `_execute_training(job, config)` L441 (~440 lines), `_persist_training_logs(job)` L884, `_persist_failure_metadata(job)` L914, `_get_failure_dir(job)` L950
- Arrows: `_create_arrow_dataset(gt_dir, dataset_dir, step, job)` L960
- Benchmark: `_run_benchmark(job)` L1004, `_execute_benchmark(job, model_path)` L1088
- Helpers: `_generate_arrow_classes(num_classes)` L1300, `_round_to_arrow_class(value, num_classes)` L1313, `_collect_benchmark_images(gt_path, model_type)` L1340

Singleton: `get_training_manager()` L1362

### `model_manager.py` (340 lines) -- Model metadata & files

**class `ModelManager`** L15:
- `__init__(models_base_path)` L18, `_validate_model_id(id)` L29, `_get_model_types_dir(type)` L34
- `list_models(type)` L40, `get_model(type, id)` L76, `save_metadata(type, id, metadata)` L113
- `delete_model(type, id)` L140, `get_active_model(type, config)` L166, `activate_model(type, id, config_path)` L193
- `get_model_path(type, id)` L249, `create_model_id(type, arch, res)` L268
- `archive_model(type, id)` L289, `get_model_metadata(type, id)` L308, `refresh()` L321

Singleton: `get_model_manager()` L335

### `inference.py` (462 lines) -- OpenVINO inference with hot-reload

- **class `Classifier`** L12: `__init__` L13, `preprocess` L22, `predict` L32 -> (label, conf), `predict_detailed` L41
- **class `Regressor`** L54: `__init__` L61, `preprocess` L69, `predict` L80 -> (value, conf), `predict_detailed` L108
- `_detect_training_mode()` L118
- **class `InferenceService`** L148: `__init__` L151, `initialize(config)` L157, `reload_models(config)` L225, `predict(model_type, image_path)` L306, `predict_detailed` L332, `get_classifier` L350, `is_reloading` L360, `models_loaded` L365, `loaded_model_types` L370, `digits_classifier` L380, `arrows_classifier` L385
- `validate_model_config(model_path, model_type, classes, resolution)` L390
- `get_inference_service()` L457 (singleton)

### `config_utils.py` (509 lines) -- YAML config with comment preservation

- `_yaml` (global) L22, `get_yaml()` L28
- `load_config(path)` L33, `save_config(config, path)` L67
- `resolve_env_vars(value)` L48 -- replaces ${VAR_NAME} with environment variable values, works recursively
- `load_config_string(yaml_string)` L81, `dump_config_string(config)` L94
- `update_config(path, updater)` L109, `validate_config(yaml_string)` L129
- `CONFIG_SCHEMA` L156-498, `get_config_schema()` L501, `get_config_schema_json()` L506

### `image_hash.py` (505 lines) -- Perceptual hashing & dedup

- `compute_dhash(image_bytes, hash_size)` L15, `hamming_distance(h1, h2)` L50
- **class `HashCache`** L64: `__init__` L67, `_load` L74, `_save` L88, `add` L96, `get_all_hashes` L101, `get_hashes_dict` L105, `remove` L109, `find_near_duplicate(new_hash, threshold)` L114, `scan_and_update` L131
- `purge_duplicates(input_dir, threshold, gt_dirs)` L147
- `cluster_images_by_hash(hashes, threshold)` L234, `select_prune_candidates(clusters, hashes)` L285
- `compute_prune_preview(gt_base, threshold)` L328, `confirm_prune(gt_base, preview)` L432

### `training_core.py` (325 lines) -- Shared training utilities

- `IMAGENET_MEAN` L49, `IMAGENET_STD` L50
- `set_all_seeds(seed)` L28, `worker_init_fn(worker_id)` L39
- `create_transforms(resolution)` L53, `stratified_split(dataset, train_ratio)` L89
- `compute_class_weights(dataset, indices, device)` L116
- `export_to_openvino(model, resolution, output_dir, filename)` L137 -- dual export: ov.convert_model() direct + ONNX dynamo=False
- `preprocess_image(image_path, resolution)` L185
- **class `RegressionArrowDataset`** L203: `__init__` L199 (note: actual class starts L203), `__len__` L219, `__getitem__` L222
- `stratified_split_regression(dataset, train_ratio)` L245
- `regression_predict(raw_output)` L278
- `circular_error(pred, true, period=10.0)` L294 -- Shortest-path error on circular dial scale
- `softmax_predict(logits, classes)` L312

### `persistence.py` (73 lines) -- JSON state persistence

- **class `StateStore`** L16: `__init__` L19, `save(data)` L23, `load()` L45, `clear()` L66

### `image_pipeline.py` (303 lines) -- Image fetching, rotation, marker alignment, ROI extraction

**class `ImagePipeline`** L20:
- Constants: `SEARCH_MARGIN` L24, `CONFIDENCE_THRESHOLD` L25
- `__init__(config)` L27
- Image fetching: `fetch_images()` L32, `fetch_whole_image()` L72, `process_whole_image(image_bytes)` L94
- Marker alignment: `_load_marker_templates(marker_count)` L157, `invalidate_marker_cache()` L186, `_align_with_markers(img, markers)` L190
- ROI extraction: `_extract_roi(img, roi, width, height)` L275

### `low_confidence_capture.py` (111 lines) -- Saves low-confidence images for training

**class `LowConfidenceCapture`** L17:
- `__init__(config)` L20
- `save_low_confidence(image_id, image_bytes, prediction, next_image_bytes)` L24

### `position_utils.py` (40 lines) -- Position ID utilities

- `get_position_ids(config)` L11 -- compute digit and arrow position IDs from config

### `scheduling.py` (97 lines) -- Cyclic and periodic background task scheduling

**class `SchedulingManager`** L15:
- `__init__(cyclic_interval, process_fn, stats_fn)` L26
- Cyclic loop: `_cyclic_loop()` L45, `start_cyclic_loop()` L56, `stop_cyclic_loop()` L64
- Stats loop: `_stats_loop()` L71, `start_stats_loop()` L84, `stop_stats_loop()` L92

### `rate_tracker.py` (112 lines) -- Rate history ring buffer for plausibility and leak detection

**class `RateTracker`** L12:
- `__init__(max_size)` L23
- Properties: `history` L30, `max_size` L35, `average_rate_per_hour` L45
- Mutation API: `add(value, timestamp)` L65, `pop_last()` L77, `replace_last(value, timestamp)` L82, `reset()` L87, `seed(value, timestamp)` L91
- Dunder: `__len__()` L101, `__repr__()` L104
- Internal: `_trim()` L109

### `leak_detector.py` (68 lines) -- Sustained consumption monitoring

**class `LeakDetector`** L16:
- `__init__(rate_tracker, config)` L24
- `check()` L28 -- checks if last N consecutive readings all show rate above threshold, returns warning message or None

### `plausibility.py` (141 lines) -- Consistency and plausibility checking

**class `PlausibilityChecker`** L17:
- `__init__(config, rate_tracker)` L25
- `check_consistency(predictions)` L29 -- validates adjacent position consistency (half vs upper-half rule), returns list of warnings
- `validate_plausibility(new_value, previous_value, last_update_time)` L76 -- checks reverse detection, rate limits, returns (is_valid, warnings)

### `__main__.py` (4 lines) -- Entry point, calls `app.main()`

---

## Templates: `watermeter/templates/`

| Template | Lines | Purpose | Key HTMX / JS |
|----------|-------|---------|----------------|
| `dashboard.html` | 50 | Main dashboard | `hx-post="/api/trigger"` L20, `hx-get="/api/status/html" hx-trigger="load, every 5s"` L40 |
| `_nav.html` | 176 | Shared nav (mobile tabs + desktop rail) | Links: /, /label, /training, /roi-config, /config-editor |
| `_theme.html` | 7 | Theme IIFE script | Reads `localStorage('theme')`, sets `data-theme` on `<html>` immediately (prevents FOUC) |
| `status_fragment.html` | 124 | HTMX fragment: value, rejected, warnings, images | Rendered server-side |
| `label.html` | 451 | Labeling + keyboard shortcuts | `loadNextImage()`, `submitLabel()`, `deleteImage()` |
| `roi_config.html` | 396 | 5-step ROI wizard + MQTT config (canvas overlay + Step 5) | Loads `roi-config.js` L393, `mqtt-config.js` L394 |
| `config_editor.html` | 390 | Monaco YAML editor | Loads `config-editor.js`, Monaco from CDN |
| `training.html` | 1568 | Training: stats, form, queue, progress, models, data tools | Loads `training.js` |

---

## Static JS: `watermeter/static/`

### `dashboard.js` (99 lines)
`toggleAutoRefresh` L1, `hideStatusMessage` L17, `showStatusMessage` L25, `setMeterValue` L34, `submitForTraining` L66

### `label.js` (257 lines)
`loadNextImage` L4, `updateProgress` L86, `submitLabel` L108, `showMessage` L143, `deleteImage` L156, `validateAndSubmit` L188

### `training.js` (1647 lines)
- State: `currentJobId`, `currentBenchmarkJobId`, `pollingInterval`, `allModels`, `activeModels`, `currentFilter`, `sortColumn`, `sortAsc`, `cachedTrainingStats` L9-17
- `CURATED_MODELS` L60-78, `initArchCombobox()` L133
- Matrix training: `matrixArchitectures` L247, `onTrainingModeToggle()` L249, `onMatrixModelTypeChange()` L267, `matrixAddArch()` L278, `matrixRemoveArch()` L285, `matrixAddGroup()` L291, `renderMatrixArchTags()` L296, `updateMatrixSummary()` L312, `initMatrixArchCombobox()` L329
- `loadTrainingStats()` L472, `getCurrentDatasetTotal()` L592, `checkForEmptyClasses()` L599
- `pollTrainingStatus()` L634 (2s interval)
- `updateBenchmarkProgress` L750, `loadBenchmarkLogs` L768, `startBenchmark` L793, `cancelBenchmark` L815
- `updateProgress` L840, `formatDuration` L894, `hideLogSectionIfIdle` L905, `loadLogs` L912
- `startTraining` L937, `startMatrixTraining` L1011, `cancelTraining` L1090
- `renderQueue` L1126, `removeFromQueue` L1160, `clearQueue` L1175
- `loadModels` L1189, `filterModels` L1213, `sortModels` L1225, `renderModels` L1238
- `activateModel` L1401, `viewModelLog` L1423, `closeLogModal` L1447, `deleteModel` L1453
- `uploadTrainingData` L1482 -- handles ZIP upload UI
- `startSyntheticGeneration` L1561, `pollSyntheticStatus` L1591, `deleteSyntheticData` L1627

### `roi-config.js` (1587 lines, inline in template)
- Setup mode (step 0): `testImageSource` L4
- Canvas/overlay state L50-85, mouse events L86-245
- Drawing: `drawCrosshair` L246, `drawBox` L271, `render` L312
- Markers: `selectMarkerInPicture` L430, `updateMarkerInputs` L437, `updateMarkerFromInput` L445, `updateMarkerPreview` L457, `saveMarkers` L495, `restartMarkers` L534, `changeMarkers` L548
- Digits: `updateDigitBoxes` L610, `selectDigitInPicture` L673, `runDigitInference` L749, `saveDigits` L806, `changeDigits` L865
- Completed steps: `updateCompletedSteps` L913
- Analogs: `updateAnalogBoxes` L1016, `selectAnalogInPicture` L1080, `runAnalogInference` L1156, `saveAnalogs` L1213, `changeAnalogs` L1272
- Rotation: `getTotalRotation` L1332, `saveRotation` L1369, `changeRotation` L1416
- Image loading: `fetchAndReload` L1436, `loadImage` L1462, `loadConfig` L1488

### `mqtt-config.js` (388 lines)
- IIFE module: `MqttConfig` with public API: `init` L368, `loadConfig` L113, `save` L163, `edit` L288, `testConnection` L297
- Helpers: `_getVal` L7, `_setVal` L12, `_setChecked` L17, `_show` L22, `_hide` L27, `_getCheckedRadio` L32, `_setCheckedRadio` L40
- Trigger mode: `_setupTriggerModeRadios` L51, `_updateTriggerFields` L63
- HA toggle: `_setupHaToggle` L76
- Password toggle: `_setupPasswordToggle` L92
- Auto-init on DOMContentLoaded L383

### `config-editor.js` (249 lines)
`initEditor` L17, `updateStatus` L78, `loadConfig` L93, `saveConfig` L118, `showMessage` L170, `toggleHaPublish` L181, `loadHaPublishState` L203, `setMeterValue` L219

---

## Static CSS: `watermeter/static/style.css` (1607 lines)

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

---

## Tests: `tests/`

### Unit Tests: `tests/unit/`

### `test_synthetic_digit_renderer.py` (138 lines)
- `TestDigitRenderer`: render returns PIL image, correct size (20x32), RGB mode, all 10 classes, white background, dark pixels, different digits differ, invalid class raises

### `test_synthetic_arrow_renderer.py` (80 lines)
- `TestArrowRenderer`: render returns PIL image, square (100x100), RGB mode, all 100 classes, red pixels (pointer), dark pixels (ticks), different classes differ, opposite pointers, invalid class raises

### `test_synthetic_transforms.py` (114 lines)
- `TestTransformPipeline`: returns PIL image, preserves size, preserves RGB, changes image, same seed = same result, different seed = different result, arrow mode fisheye, digit mode no fisheye, pixel values in range

### `test_synthetic_generator.py` (194 lines)
- `TestSyntheticGenerator`: generate digits, generate arrows (all 100 classes), generate both, valid JPEG output, reproducible with seed, progress callback, synth_ prefix, does not overwrite existing
- `TestDeleteSynthetic`: removes synth files, preserves real files, returns correct count

### `test_synthetic_routes.py` (165 lines)
- `TestSyntheticRoutes`: generate endpoint exists, returns job_id, invalid type 422, status endpoint exists, status returns fields, delete endpoint exists, delete returns count, invalid type 400, conflict when running 409, conflict when training 409, delete conflict when running 409

### `test_api_routes.py` (266 lines)
- `TestConfigEndpoints` L13, `TestTrainingStatus` L83, `TestModelEndpoints` L103, `TestLabelValidation` L157

### `test_arrow_regression.py` (431 lines)
- `TestRegressionArrowDataset` L131, `TestStratifiedSplitRegression` L178, `TestRegressionPredict` L220, `TestRegressor` L259, `TestDetectTrainingMode` L321, `TestTrainingConfigValidation` L372, `TestValidateModelConfigContinuous` L407

### `test_circular_error.py` (52 lines)
- `TestCircularError` L5: shortest-path error on circular scale

### `test_config_reload.py` (165 lines)
- `TestReloadConfig` L50: config reload behavior

### `test_config_utils.py` (165 lines)
- `TestLoadSaveConfig` L17, `TestConfigString` L50, `TestUpdateConfig` L81, `TestValidateConfig` L113, `TestConfigSchema` L153

### `test_confirmation.py` (791 lines)
- `TestShouldRequestConfirmation` L166, `TestPublishConfirmationRequest` L241, `TestHandleConfirmationResponse` L358, `TestConfirmationTimeout` L478, `TestGetConfirmationStatus` L566, `TestResetClearsConfirmation` L615, `TestMqttMessageRouting` L635, `TestMqttConnectSubscription` L674, `TestDisabledMode` L711, `TestProcessReadingSkipsPending` L731, `TestRejectWithNonePreviousValue` L765

### `test_cross_arrow_consistency.py` (379 lines)
- `TestCheckCrossArrowConsistency` L64, `TestCrossArrowIntegration` L247

### `test_image_hash.py` (190 lines)
- `TestComputeDhash` L9, `TestHammingDistance` L81, `TestHashCache` L100

### `test_leak_detection.py` (322 lines)
- `TestCreateTransforms` L6, `TestCheckSustainedConsumption` L101, `TestLeakWarningMqtt` L208, `TestLeakWarningStateManagement` L268

### `test_leak_detector.py` (115 lines)
- `TestLeakDetectorInit` L35, `TestLeakDetectorCheck` L42

### `test_marker_alignment.py` (478 lines)
- `TestFewerThanTwoMarkers` L196, `TestMissingTemplateFiles` L213, `TestLowConfidenceMatch` L239, `TestAlreadyAlignedImage` L267, `TestShiftedImageCorrected` L299, `TestTemplateCaching` L346, `TestCacheInvalidation` L374, `TestSearchRegionNearEdge` L413

### `test_mislabel.py` (714 lines)
- `TestScanMislabeled` L26, `TestConfirmMislabeled` L275, `TestMakeThumbnailBase64` L426, `TestMislabelAPIEndpoints` L451

### `test_model_manager.py` (271 lines)
- `TestModelManagerValidation` L31, `TestModelManagerCRUD` L62, `TestModelManagerActivation` L132, `TestModelManagerHelpers` L229

### `test_persistence.py` (93 lines)
- `TestStateStore` L11

### `test_photo_master.py` (109 lines)
- `TestPointerExtractor` L29, `TestArrowCompositor` L65, `TestBuildArrowCompositor` L97

### `test_plausibility.py` (163 lines)
- `TestCheckConsistency` L46, `TestValidatePlausibility` L100

### `test_position_utils.py` (131 lines)
- `TestGetPositionIds` L6

### `test_prune.py` (653 lines)
- `TestClusterImagesByHash` L13, `TestSelectPruneCandidates` L88, `TestComputePrunePreview` L150, `TestConfirmPrune` L265, `TestConfirmPrunePathTraversal` L376, `TestHashCacheExtensions` L458, `TestPruneAPIEndpoints` L501

### `test_pure_functions.py` (136 lines)
- `TestSafeSubpath` L16, `TestGenerateArrowClasses` L60, `TestRoundToArrowClass` L97

### `test_rate_tracker.py` (168 lines)
- `TestRateTrackerInit` L10, `TestRateTrackerAdd` L31, `TestRateTrackerMutations` L64, `TestRateTrackerAverageRate` L113, `TestRateTrackerMaxSize` L153

### `test_training_augmentation.py` (44 lines)
- `TestCreateTransforms` L6: verifies transform pipeline output types and sizes

### `test_training_config.py` (85 lines)
- `TestTrainingConfigLearningRate` L6: validates learning rate field in TrainingConfig

### `test_trigger_mode.py` (416 lines)
- `TestTriggerConfigSchema` L45, `TestTriggerConfigParsing` L65, `TestTriggerModeDefaults` L101, `TestCyclicLoop` L125, `TestMqttSubscriptionConditional` L234, `TestMqttV2Api` L293

### `test_value_correction.py` (721 lines)
- `TestPredictDetailed` L97, `TestHelperMethods` L151, `TestConsistencyImprovement` L234, `TestSignalScoring` L290, `TestCorrectionEngine` L467

---

### Integration Tests: `tests/integration/`

### `test_smoke.py` (41 lines)
- `test_health_endpoint`, `test_status_endpoint`, `test_dashboard_page`, `test_training_page`, `test_training_status_endpoint`

### `test_config.py` (56 lines)
- `test_get_config`, `test_get_config_schema`, `test_save_invalid_yaml`, `test_config_roundtrip`

### `test_models.py` (51 lines)
- `test_list_architectures`, `test_list_architectures_short_query_returns_empty`, `test_list_digits_models`, `test_list_arrows_models`, `test_training_data_stats`, `test_model_not_found`

### `test_training.py` (165 lines)
- `test_training_e2e` L58, `test_training_cancel` L106, `test_training_queue` L134

### `test_benchmark.py` (148 lines)
- `test_benchmark_digits` L72, `test_benchmark_arrows` L103, `test_benchmark_cancel` L124

### `test_synthetic.py` (105 lines)
- `test_synthetic_status_idle`, `test_synthetic_generation_digits` (generate + poll + cleanup), `test_synthetic_cannot_run_twice` (409 concurrency guard), `test_synthetic_delete_nonexistent`, `test_synthetic_invalid_type_on_delete`

---

### Regression Tests: `tests/regression/`

### `test_config_comments.py` (39 lines)
- `test_inline_comment_preserved`, `test_block_comment_preserved`, `test_comment_survives_file_round_trip`

### `test_nan_class_label.py` (22 lines)
- `test_arrow_classes_do_not_contain_nan`: verifies NAN handling in arrow class generation

---

### Export Tests: `tests/export/`

### `test_export_openvino.py` (80 lines)
- `TestExportToOpenvino` L8: tests dual-path OpenVINO export from PyTorch model
