# Codebase Map

> Auto-generated reference. Line numbers as of 2026-02-18.

## Python Package: `watermeter/`

### `watermeter/app.py` (199 lines) -- FastAPI app, lifespan, router wiring

| Line | Type | Name | Description |
|------|------|------|-------------|
| 22 | func | `safe_subpath(base, *parts)` | Path traversal guard; raises ValueError if outside base |
| 31 | var | `_background_tasks: set` | Prevents GC of fire-and-forget asyncio tasks |
| 34 | func | `_create_background_task(coro)` | Creates asyncio task, registers in `_background_tasks` |
| 43 | func | `lifespan(app)` | Startup/shutdown: inference init, MQTT, cyclic loop, dedup, initial reading |
| 105 | var | `tags_metadata` | OpenAPI tag definitions for grouping endpoints |
| 145 | var | `app` | FastAPI application instance |
| 161 | — | router includes | pages, service, config, roi, label, training, models, synthetic, mqtt |
| 182 | func | `main()` | Uvicorn entry point; reads config for host/port |

---

### `watermeter/routes/pages.py` (114 lines) -- HTML page routes

| Line | Type | Name | Description |
|------|------|------|-------------|
| 18 | route | `GET /` | Dashboard; redirects to `/roi-config?setup=1` if no reference image |
| 38 | route | `GET /label` | Labeling interface page |
| 50 | route | `GET /roi-config` | ROI wizard; passes `setup_mode` and `image_src` to template |
| 69 | route | `GET /config-editor` | Monaco YAML editor page |
| 81 | route | `GET /training` | Training management page |
| 93 | route | `GET /api/status/html` | HTMX status fragment; copies state dict to avoid template mutation |

---

### `watermeter/routes/service.py` (229 lines) -- Core service API

| Line | Type | Name | Description |
|------|------|------|-------------|
| 34 | class | `TrainingSubmission` | Pydantic model for submit-training payload |
| 43 | class | `SetValueRequest` | Pydantic model for manual value override |
| 49 | route | `GET /api/status` | Returns full current_state JSON |
| 63 | route | `POST /api/trigger` | Trigger single reading cycle immediately |
| 88 | route | `POST /api/reset` | Reset previous value in state |
| 101 | route | `POST /api/set-value` | Manually set meter value |
| 135 | route | `POST /api/toggle-ha-publish` | Toggle Home Assistant MQTT publishing |
| 149 | route | `POST /api/submit-training` | Submit current reading as training label |
| 197 | route | `GET /api/confirmation/status` | Get pending confirmation request status |
| 215 | route | `GET /health` | Health check endpoint |

---

### `watermeter/routes/config.py` (101 lines) -- Config management

| Line | Type | Name | Description |
|------|------|------|-------------|
| 25 | route | `GET /api/config` | Return raw YAML config string |
| 48 | route | `POST /api/config/save` | Validate and save YAML config; triggers service reload |
| 93 | route | `GET /api/config/schema.json` | Return JSON schema for config validation |

---

### `watermeter/routes/roi.py` (791 lines) -- ROI configuration wizard

| Line | Type | Name | Description |
|------|------|------|-------------|
| 25 | class | `RotationSubmission` | Pydantic model for rotation degrees |
| 29 | class | `MarkerBox` | Pydantic model for a single marker bounding box |
| 36 | class | `MarkersSubmission` | Pydantic model for list of markers |
| 40 | class | `DigitRoi` | Pydantic model for a single digit ROI |
| 47 | class | `DigitsSubmission` | Pydantic model for list of digit ROIs |
| 52 | class | `SingleRoiSubmission` | Pydantic model for single generic ROI |
| 59 | class | `AnalogRoi` | Pydantic model for a single analog dial ROI |
| 66 | class | `AnalogsSubmission` | Pydantic model for list of analog ROIs |
| 74 | func | `_load_rotated_reference()` | Load reference image applying current rotation config |
| 99 | route | `POST /api/roi/fetch-image` | Fetch image from URL and save as reference |
| 139 | route | `POST /api/roi/image-source` | Set image source URL in config |
| 202 | route | `GET /api/roi/reference-image` | Serve current reference image as JPEG |
| 220 | route | `GET /api/roi/config` | Return current ROI configuration |
| 240 | route | `POST /api/roi/rotation` | Save rotation degrees to config |
| 271 | route | `DELETE /api/roi/rotation` | Reset rotation to zero |
| 302 | route | `POST /api/roi/markers` | Save marker bounding boxes |
| 376 | route | `DELETE /api/roi/markers` | Clear all markers |
| 416 | route | `GET /api/roi/marker-image/{id}` | Serve cropped marker image |
| 432 | route | `POST /api/roi/digits` | Save digit ROI definitions |
| 500 | route | `DELETE /api/roi/digits` | Clear all digit ROIs |
| 538 | route | `GET /api/roi/digit-image/{id}` | Serve cropped digit ROI image |
| 554 | route | `POST /api/roi/digit-preview` | Run inference on digit ROI, return preview |
| 614 | route | `POST /api/roi/analogs` | Save analog ROI definitions |
| 680 | route | `DELETE /api/roi/analogs` | Clear all analog ROIs |
| 718 | route | `GET /api/roi/analog-image/{id}` | Serve cropped analog ROI image |
| 734 | route | `POST /api/roi/analog-preview` | Run inference on analog ROI, return preview |

---

### `watermeter/routes/mqtt.py` (206 lines) -- MQTT configuration routes

| Line | Type | Name | Description |
|------|------|------|-------------|
| 31 | func | `_has_unresolved_tokens(value)` | Check for unresolved `${...}` env-var tokens |
| 36 | route | `GET /api/mqtt/config` | Return raw config (mqtt, trigger, homeassistant sections); does NOT resolve env vars |
| 71 | route | `POST /api/mqtt/config` | Save mqtt/trigger/ha sections; triggers service reload |
| 124 | route | `POST /api/mqtt/test` | Test MQTT connection; resolves env vars in credentials; warns on unresolved tokens |

---

### `watermeter/routes/label.py` (218 lines) -- Labeling interface API

| Line | Type | Name | Description |
|------|------|------|-------------|
| 21 | class | `LabelSubmission` | Pydantic model for label submission payload |
| 27 | class | `DeleteSubmission` | Pydantic model for image deletion request |
| 32 | route | `GET /api/label/next-image` | Return next unlabeled image for labeling |
| 92 | route | `POST /api/label/submit` | Accept label for an image; moves to ground_truth |
| 183 | route | `POST /api/label/delete` | Delete an unlabeled image |

---

### `watermeter/routes/training.py` (411 lines) -- Training job management + ZIP upload

| Line | Type | Name | Description |
|------|------|------|-------------|
| 29 | class | `TrainingConfig` | Pydantic model; validators for seeds, training_mode, learning_rate |
| 62 | route | `GET /api/training/status` | Return current training/benchmark status |
| 85 | route | `POST /api/training/start` | Enqueue a new training job |
| 112 | route | `POST /api/training/cancel` | Cancel active or queued training job |
| 141 | route | `DELETE /api/training/queue/{index}` | Remove specific item from queue |
| 160 | route | `DELETE /api/training/queue` | Clear entire training queue |
| 176 | route | `POST /api/benchmark/cancel` | Cancel active benchmark job |
| 200 | route | `GET /api/training/logs/{job_id}` | Return training logs for a job |
| 219 | route | `GET /api/training/progress/{job_id}` | Return training progress for a job |
| 244 | func | `_detect_and_sort(images, data_type)` | Detect ZIP format (subdirectory or prefix-based) |
| 309 | route | `POST /api/training-data/upload` | Upload ZIP of training images; supports subdir and prefix formats |

---

### `watermeter/routes/models.py` (706 lines) -- Model management + training data tools

| Line | Type | Name | Description |
|------|------|------|-------------|
| 25 | route | `GET /api/models/architectures` | List available model architectures |
| 39 | route | `GET /api/models` | List all models (optionally filtered by type) |
| 76 | route | `GET /api/models/{type}/{id}` | Get metadata for a specific model |
| 98 | route | `POST /api/models/{type}/{id}/activate` | Set model as active in config |
| 137 | route | `POST /api/models/{type}/{id}/archive` | Archive a model (move to archived/) |
| 159 | route | `DELETE /api/models/{type}/{id}` | Delete a model permanently |
| 181 | route | `GET /api/models/{type}/{id}/logs` | Return training log for a model |
| 205 | route | `POST /api/models/{type}/{id}/benchmark` | Start benchmark job for a model |
| 226 | route | `GET /api/training-data/stats` | Return training data statistics per class |
| 272 | route | `POST /api/training-data/dedup` | Run deduplication on training data |
| 309 | route | `POST /api/training-data/prune/preview` | Preview which images would be pruned |
| 365 | route | `POST /api/training-data/prune/confirm` | Confirm and execute pruning |
| 422 | func | `_make_thumbnail_base64(path)` | Create base64-encoded JPEG thumbnail |
| 435 | func | `scan_mislabeled(model_type, training_path)` | Scan for mislabeled training images using active model |
| 518 | func | `confirm_mislabeled(model_type, training_path, selected_paths)` | Delete confirmed mislabeled images |
| 582 | route | `POST /api/training-data/mislabel/scan` | Scan training data for mislabeled images |
| 632 | route | `POST /api/training-data/mislabel/confirm` | Delete selected mislabeled images |

---

### `watermeter/routes/synthetic.py` (157 lines) -- Synthetic data generation API

| Line | Type | Name | Description |
|------|------|------|-------------|
| 19 | var | `_generation_lock` | Threading lock prevents concurrent generation |
| 20 | var | `_generation_status` | Module-level generation status dict |
| 30 | class | `SyntheticConfig` | Pydantic model; validators for type, count_per_class |
| 50 | func | `_run_generation(config, job_id)` | Background thread target for generation |
| 89 | route | `POST /api/synthetic/generate` | Start synthetic data generation; 409 if running or training |
| 129 | route | `GET /api/synthetic/status` | Return current generation status |
| 140 | route | `DELETE /api/synthetic/{type}` | Delete all synthetic images of given type |

---

### `watermeter/watermeter_service.py` (1918 lines) -- Main service orchestration

| Line | Type | Name | Description |
|------|------|------|-------------|
| 243 | class | `WatermeterService` | Main service orchestrating all subsystems |
| 246 | func | `__init__(config_path)` | Loads config, delegates to component classes |
| 405 | func | `fetch_images()` | Delegates to ImagePipeline |
| 410 | func | `fetch_whole_image()` | Delegates to ImagePipeline |
| 415 | func | `process_whole_image(img_bytes)` | Delegates to ImagePipeline |
| 420 | func | `invalidate_marker_cache()` | Delegates to ImagePipeline |
| 424 | func | `run_inference(images)` | Run OpenVINO inference on image set |
| 495 | func | `calculate_total(predictions)` | Compute total meter reading from predictions |
| 548 | func | `check_consistency(predictions)` | Delegates to PlausibilityChecker |
| 552 | func | `validate_plausibility(total, ...)` | Delegates to PlausibilityChecker |
| 560 | func | `_check_sustained_consumption()` | Delegates to LeakDetector |
| 566 | func | `_get_confirmation_config()` | Read confirmation section from config |
| 580 | func | `_should_request_confirmation()` | Determine if confirmation request is needed |
| 621 | func | `_publish_confirmation_request()` | Publish MQTT confirmation request message |
| 674 | func | `_cancel_confirmation_timer()` | Cancel pending confirmation timeout timer |
| 680 | func | `_confirmation_timeout()` | Synchronous timeout wrapper |
| 687 | func | `_do_confirmation_timeout()` | Handle confirmation timeout logic |
| 728 | func | `_handle_confirmation_response(payload)` | Process MQTT confirmation response |
| 832 | func | `get_confirmation_status()` | Return current confirmation state dict |
| 857 | func | `_get_ordered_position_ids()` | Get digit/arrow IDs in display order |
| 862 | func | `_estimate_expected_range()` | Estimate expected value range from history |
| 881 | func | `_recalculate_with_replacement()` | Recalculate total substituting corrected prediction |
| 912 | func | `_check_consistency_improvement()` | Check if correction improves consistency |
| 963 | func | `_check_cross_arrow_consistency()` | Validate cross-arrow consistency constraints |
| 1017 | func | `correct_predictions(predictions, ...)` | BL-04: auto-correct low-confidence predictions |
| 1127 | func | `save_low_confidence(predictions, images)` | Delegates to LowConfidenceCapture |
| 1133 | func | `process_reading()` | Main pipeline: fetch, infer, validate, correct, publish |
| 1418 | func | `publish_to_mqtt(status)` | Publish reading result to MQTT topics |
| 1491 | func | `reset_previous_value()` | Clear previous value from state |
| 1516 | func | `set_manual_value(value)` | Override meter value manually |
| 1585 | func | `toggle_ha_publish()` | Toggle HA MQTT publishing flag |
| 1648 | func | `publish_discovery()` | Emit Home Assistant MQTT discovery messages |
| 1711 | func | `on_mqtt_connect()` | MQTT connect callback; subscribes to topics |
| 1742 | func | `on_mqtt_disconnect(...)` | MQTT disconnect callback |
| 1749 | func | `on_mqtt_message()` | Route incoming MQTT messages |
| 1785 | func | `start_mqtt()` | Initialize and start MQTT client |
| 1828 | func | `reload_config(new_config)` | Hot-reload config; reconnect MQTT if needed |
| 1883 | func | `stop_mqtt()` | Disconnect and stop MQTT client |
| 1907 | func | `get_service()` | Singleton accessor |

---

### `watermeter/training_manager.py` (1367 lines) -- Training/benchmark orchestration

| Line | Type | Name | Description |
|------|------|------|-------------|
| 20 | class | `JobStatus` | Enum: pending, running, done, failed, cancelled |
| 30 | class | `TrainingJob` | Dataclass for a training job |
| 77 | class | `BenchmarkJob` | Dataclass for a benchmark job |
| 125 | class | `TrainingManager` | Manages training queue and benchmark execution |
| 128 | func | `__init__()` | Initialize queues, locks, status tracking |
| 139 | func | `start_training(config)` | Enqueue or start immediately |
| 156 | func | `_start_training_now(config)` | Launch training thread immediately |
| 171 | func | `start_benchmark(model_type, model_id)` | Start benchmark in background thread |
| 204 | func | `cancel_training(job_id, clear_queue)` | Cancel active or queued training |
| 220 | func | `cancel_benchmark(job_id)` | Cancel active benchmark |
| 237 | func | `get_training_status()` | Return current training status dict |
| 243 | func | `get_benchmark_status()` | Return current benchmark status dict |
| 249 | func | `get_queue()` | Return list of queued training jobs |
| 254 | func | `remove_from_queue(index)` | Remove item at index from queue |
| 262 | func | `clear_queue()` | Remove all queued jobs |
| 269 | func | `get_job_logs(job_id)` | Return log text for a job |
| 276 | func | `_run_training(config)` | Thread target: execute + process next in queue |
| 396 | func | `_process_next_in_queue()` | Start next queued job if idle |
| 415 | func | `_run_auto_benchmarks()` | Run benchmarks automatically after training |
| 441 | func | `_execute_training(job, config)` | Core training execution (~440 lines) |
| 884 | func | `_persist_training_logs(job)` | Save training logs to model directory |
| 914 | func | `_persist_failure_metadata(job)` | Save failure metadata to model directory |
| 950 | func | `_get_failure_dir(job)` | Get directory for failed job artifacts |
| 960 | func | `_create_arrow_dataset(gt_dir, dataset_dir, step, job)` | Build subsampled arrow dataset |
| 1004 | func | `_run_benchmark(job)` | Thread target for benchmark |
| 1088 | func | `_execute_benchmark(job, model_path)` | Core benchmark execution logic |
| 1300 | func | `_generate_arrow_classes(num_classes)` | Generate arrow class label strings |
| 1313 | func | `_round_to_arrow_class(value, num_classes)` | Round value to nearest arrow class |
| 1340 | func | `_collect_benchmark_images(gt_path, model_type)` | Collect images for benchmark |
| 1362 | func | `get_training_manager()` | Singleton accessor |

---

### `watermeter/model_manager.py` (340 lines) -- Model metadata and files

| Line | Type | Name | Description |
|------|------|------|-------------|
| 15 | class | `ModelManager` | Manages model files, metadata, and activation |
| 18 | func | `__init__(models_base_path)` | Initialize with base path |
| 29 | func | `_validate_model_id(id)` | Validate model ID format |
| 34 | func | `_get_model_types_dir(type)` | Get directory for model type |
| 40 | func | `list_models(type)` | List all models of a type |
| 76 | func | `get_model(type, id)` | Get model metadata by ID |
| 113 | func | `save_metadata(type, id, metadata)` | Write metadata.json for a model |
| 140 | func | `delete_model(type, id)` | Delete model directory permanently |
| 166 | func | `get_active_model(type, config)` | Get active model from config |
| 193 | func | `activate_model(type, id, config_path)` | Set model as active in config file |
| 249 | func | `get_model_path(type, id)` | Get filesystem path for model |
| 268 | func | `create_model_id(type, arch, res)` | Generate unique model ID |
| 289 | func | `archive_model(type, id)` | Move model to archived subdirectory |
| 308 | func | `get_model_metadata(type, id)` | Read metadata.json for a model |
| 321 | func | `refresh()` | Invalidate internal caches |
| 335 | func | `get_model_manager()` | Singleton accessor |

---

### `watermeter/inference.py` (462 lines) -- OpenVINO inference with hot-reload

| Line | Type | Name | Description |
|------|------|------|-------------|
| 12 | class | `Classifier` | Softmax classifier wrapping OpenVINO model |
| 13 | func | `__init__` | Load compiled model |
| 22 | func | `preprocess` | Resize and normalize image |
| 32 | func | `predict` | Returns (label, conf) tuple |
| 41 | func | `predict_detailed` | Returns label, conf, and all class scores |
| 54 | class | `Regressor` | Regression classifier for analog dials |
| 61 | func | `__init__` | Load compiled model |
| 69 | func | `preprocess` | Resize and normalize image |
| 80 | func | `predict` | Returns (value, conf) tuple |
| 108 | func | `predict_detailed` | Returns value, conf, and all class scores |
| 118 | func | `_detect_training_mode()` | Detect if digits or arrows training mode |
| 148 | class | `InferenceService` | Hot-reloadable inference service singleton |
| 151 | func | `__init__` | Initialize empty model references |
| 157 | func | `initialize(config)` | Load models from config on startup |
| 225 | func | `reload_models(config)` | Reload models without restarting service |
| 306 | func | `predict(model_type, image_path)` | Predict with named model type |
| 332 | func | `predict_detailed` | Detailed prediction with all scores |
| 350 | func | `get_classifier` | Get classifier by model type |
| 360 | func | `is_reloading` | Property: True if reload in progress |
| 365 | func | `models_loaded` | Property: True if any models loaded |
| 370 | func | `loaded_model_types` | Property: list of loaded model types |
| 380 | func | `digits_classifier` | Property: digits classifier instance |
| 385 | func | `arrows_classifier` | Property: arrows classifier instance |
| 390 | func | `validate_model_config(model_path, model_type, classes, resolution)` | Validate model file and config |
| 457 | func | `get_inference_service()` | Singleton accessor |

---

### `watermeter/config_utils.py` (508 lines) -- YAML config with comment preservation

| Line | Type | Name | Description |
|------|------|------|-------------|
| 22 | var | `_yaml` | Global ruamel.yaml instance |
| 28 | func | `get_yaml()` | Get or create ruamel.yaml instance |
| 33 | func | `load_config(path)` | Load config file preserving comments |
| 48 | func | `resolve_env_vars(value)` | Replace `${VAR}` with env values recursively |
| 67 | func | `save_config(config, path)` | Save config preserving comments |
| 81 | func | `load_config_string(yaml_string)` | Parse YAML string to dict |
| 94 | func | `dump_config_string(config)` | Serialize config dict to YAML string |
| 109 | func | `update_config(path, updater)` | Load, apply updater function, save |
| 129 | func | `validate_config(yaml_string)` | Validate YAML string against schema |
| 156 | var | `CONFIG_SCHEMA` | Full JSON schema for config.yaml (L156-498) |
| 501 | func | `get_config_schema()` | Return CONFIG_SCHEMA dict |
| 506 | func | `get_config_schema_json()` | Return CONFIG_SCHEMA as JSON string |

---

### `watermeter/image_hash.py` (505 lines) -- Perceptual hashing and dedup

| Line | Type | Name | Description |
|------|------|------|-------------|
| 15 | func | `compute_dhash(image_bytes, hash_size)` | Compute difference hash of image bytes |
| 50 | func | `hamming_distance(h1, h2)` | Compute bit distance between two hashes |
| 64 | class | `HashCache` | Persistent hash cache for dedup operations |
| 67 | func | `__init__` | Initialize with cache file path |
| 74 | func | `_load` | Load cache from JSON file |
| 88 | func | `_save` | Save cache to JSON file |
| 96 | func | `add` | Add hash for an image path |
| 101 | func | `get_all_hashes` | Return list of all cached hashes |
| 105 | func | `get_hashes_dict` | Return dict of path->hash |
| 109 | func | `remove` | Remove hash for a path |
| 114 | func | `find_near_duplicate(new_hash, threshold)` | Find near-duplicate in cache |
| 131 | func | `scan_and_update` | Scan directory and update cache |
| 147 | func | `purge_duplicates(input_dir, threshold, gt_dirs)` | Remove duplicates from input dir |
| 234 | func | `cluster_images_by_hash(hashes, threshold)` | Group near-duplicate images into clusters |
| 285 | func | `select_prune_candidates(clusters, hashes)` | Pick images to delete from clusters |
| 328 | func | `compute_prune_preview(gt_base, threshold)` | Preview which images would be pruned |
| 432 | func | `confirm_prune(gt_base, preview)` | Execute pruning based on preview result |

---

### `watermeter/synthetic_generator.py` (507 lines) -- Synthetic training data generator

| Line | Type | Name | Description |
|------|------|------|-------------|
| 19 | var | `DIGIT_CLASSES` | List of digit class labels (0-9 + NAN) |
| 20 | var | `ARROW_CLASSES` | List of 100 arrow class labels (0.00-0.99) |
| 23 | func | `_deterministic_seed(cls, i)` | Generate deterministic seed from class and index |
| 29 | func | `_load_font(size)` | Load TrueType font for digit rendering |
| 36 | class | `DigitRenderer` | Renders digit images from font |
| 48 | func | `__init__()` | Initialize font and color settings |
| 51 | func | `render(digit_class, seed)` | Render digit image from font |
| 89 | class | `DigitCompositor` | Composites digit onto photo background |
| 110 | func | `__init__(background_dir)` | Load background photos |
| 128 | func | `render(digit_class, seed)` | Composite digit onto photo background |
| 174 | class | `ArrowRenderer` | Renders 100x100px dial image with ticks and red pointer |
| 177 | func | `__init__()` | Initialize dial parameters |
| 180 | func | `render(arrow_class, seed)` | Render dial at given angle class |
| 248 | class | `TransformPipeline` | Applies geometric, color, and noise transforms |
| 251 | func | `__init__(seed)` | Initialize with RNG seed |
| 255 | func | `apply(img, mode)` | Apply all transforms (offset, perspective, fisheye, color, noise) |
| 379 | class | `_MultiCompositor` | Wraps multiple compositors for arrow rendering |
| 382 | func | `__init__(compositors, seed)` | Initialize with compositor pool |
| 386 | func | `render(arrow_class, seed)` | Delegate to random compositor |
| 391 | class | `SyntheticGenerator` | Orchestrates synthetic data generation |
| 394 | func | `__init__(base_dir)` | Initialize with output base directory |
| 397 | func | `_get_digit_renderer()` | Get or create digit renderer |
| 406 | func | `_get_arrow_renderer()` | Get or create arrow renderer |
| 435 | func | `generate(type, count_per_class, seed, progress_callback)` | Generate synthetic images to ground_truth/ |
| 490 | func | `delete_synthetic(type)` | Remove all `synth_*` files from ground_truth |

---

### `watermeter/photo_master.py` (143 lines) -- Photo-based master image for arrow compositing

| Line | Type | Name | Description |
|------|------|------|-------------|
| 18 | func | `extract_pointer_mask(img)` | HSV thresholding to isolate red pointer; returns binary mask |
| 45 | func | `inpaint_background(img, mask)` | Remove pointer with inpainting; returns clean background |
| 62 | func | `extract_pointer_template(img, mask)` | Crop pointer region to template |
| 75 | class | `ArrowCompositor` | Composites real pointer template onto inpainted backgrounds |
| 117 | func | `build_arrow_compositor(photos_dir, ...)` | Factory: load photos, extract pointers, return compositor |

---

### `watermeter/training_core.py` (325 lines) -- Shared training utilities

| Line | Type | Name | Description |
|------|------|------|-------------|
| 28 | func | `set_all_seeds(seed)` | Set RNG seeds for reproducibility |
| 39 | func | `worker_init_fn(worker_id)` | DataLoader worker seed initializer |
| 49 | var | `IMAGENET_MEAN` | ImageNet normalization mean values |
| 50 | var | `IMAGENET_STD` | ImageNet normalization std values |
| 53 | func | `create_transforms(resolution)` | Create train and val transform pipelines |
| 89 | func | `stratified_split(dataset, train_ratio)` | Split dataset preserving class ratios |
| 116 | func | `compute_class_weights(dataset, indices, device)` | Compute per-class loss weights |
| 137 | func | `export_to_openvino(model, resolution, output_dir, filename)` | Dual export: direct + ONNX dynamo=False |
| 185 | func | `preprocess_image(image_path, resolution)` | Load and preprocess single image |
| 203 | class | `RegressionArrowDataset` | Dataset for regression-based arrow classification |
| 219 | func | `__len__` | Return dataset length |
| 222 | func | `__getitem__` | Return (image, label) pair |
| 245 | func | `stratified_split_regression(dataset, train_ratio)` | Split regression dataset by class |
| 278 | func | `regression_predict(raw_output)` | Convert regression output to class prediction |
| 294 | func | `circular_error(pred, true, period=10.0)` | Shortest-path error on circular dial scale |
| 312 | func | `softmax_predict(logits, classes)` | Convert logits to (label, conf) via softmax |

---

### `watermeter/image_pipeline.py` (303 lines) -- Image fetching, rotation, marker alignment, ROI extraction

| Line | Type | Name | Description |
|------|------|------|-------------|
| 20 | class | `ImagePipeline` | Image fetch, alignment, and ROI extraction |
| 24 | var | `SEARCH_MARGIN` | Pixel margin for marker search region |
| 25 | var | `CONFIDENCE_THRESHOLD` | Minimum confidence for marker matching |
| 27 | func | `__init__(config)` | Initialize with config dict |
| 32 | func | `fetch_images()` | Fetch and process full image pipeline |
| 72 | func | `fetch_whole_image()` | Fetch raw whole image bytes |
| 94 | func | `process_whole_image(image_bytes)` | Process fetched image: rotate, align, extract ROIs |
| 157 | func | `_load_marker_templates(marker_count)` | Load marker template images from disk |
| 186 | func | `invalidate_marker_cache()` | Clear cached marker templates |
| 190 | func | `_align_with_markers(img, markers)` | Align image using marker template matching |
| 275 | func | `_extract_roi(img, roi, width, height)` | Extract ROI crop from full image |

---

### `watermeter/low_confidence_capture.py` (111 lines) -- Saves low-confidence images for training

| Line | Type | Name | Description |
|------|------|------|-------------|
| 17 | class | `LowConfidenceCapture` | Saves low-confidence images for active learning |
| 20 | func | `__init__(config)` | Initialize with config dict |
| 24 | func | `save_low_confidence(image_id, image_bytes, prediction, next_image_bytes)` | Save image if below confidence threshold |

---

### `watermeter/persistence.py` (73 lines) -- JSON state persistence

| Line | Type | Name | Description |
|------|------|------|-------------|
| 16 | class | `StateStore` | Atomic JSON state file persistence |
| 19 | func | `__init__` | Initialize with file path |
| 23 | func | `save(data)` | Atomically write state to JSON file |
| 45 | func | `load()` | Load state from JSON file |
| 66 | func | `clear()` | Delete state file |

---

### `watermeter/plausibility.py` (141 lines) -- Consistency and plausibility checking

| Line | Type | Name | Description |
|------|------|------|-------------|
| 17 | class | `PlausibilityChecker` | Validates readings for consistency and plausibility |
| 25 | func | `__init__(config, rate_tracker)` | Initialize with config and rate tracker |
| 29 | func | `check_consistency(predictions)` | Validate adjacent position consistency (half vs upper-half rule) |
| 76 | func | `validate_plausibility(new_value, previous_value, last_update_time)` | Check reverse, rate limits; returns (is_valid, warnings) |

---

### `watermeter/rate_tracker.py` (112 lines) -- Rate history ring buffer

| Line | Type | Name | Description |
|------|------|------|-------------|
| 12 | class | `RateTracker` | Ring buffer for rate history used by plausibility and leak detection |
| 23 | func | `__init__(max_size)` | Initialize with max buffer size |
| 30 | func | `history` | Property: list of (value, timestamp) tuples |
| 35 | func | `max_size` | Property: maximum buffer size |
| 45 | func | `average_rate_per_hour` | Property: computed average rate |
| 65 | func | `add(value, timestamp)` | Append new reading |
| 77 | func | `pop_last()` | Remove and return last reading |
| 82 | func | `replace_last(value, timestamp)` | Replace last reading in-place |
| 87 | func | `reset()` | Clear all history |
| 91 | func | `seed(value, timestamp)` | Initialize history with single reading |
| 101 | func | `__len__()` | Return number of readings |
| 104 | func | `__repr__()` | String representation |
| 109 | func | `_trim()` | Enforce max_size constraint |

---

### `watermeter/leak_detector.py` (68 lines) -- Sustained consumption monitoring

| Line | Type | Name | Description |
|------|------|------|-------------|
| 16 | class | `LeakDetector` | Monitors for sustained non-zero consumption (leak) |
| 24 | func | `__init__(rate_tracker, config)` | Initialize with rate tracker and config |
| 28 | func | `check()` | Returns warning message if N consecutive readings exceed threshold |

---

### `watermeter/scheduling.py` (97 lines) -- Cyclic and periodic background task scheduling

| Line | Type | Name | Description |
|------|------|------|-------------|
| 15 | class | `SchedulingManager` | Manages cyclic reading loop and stats loop |
| 26 | func | `__init__(cyclic_interval, process_fn, stats_fn)` | Initialize with callbacks |
| 45 | func | `_cyclic_loop()` | Threaded loop: call process_fn on interval |
| 56 | func | `start_cyclic_loop()` | Start background cyclic thread |
| 64 | func | `stop_cyclic_loop()` | Stop cyclic thread |
| 71 | func | `_stats_loop()` | Threaded loop: call stats_fn on interval |
| 84 | func | `start_stats_loop()` | Start background stats thread |
| 92 | func | `stop_stats_loop()` | Stop stats thread |

---

### `watermeter/position_utils.py` (40 lines) -- Position ID utilities

| Line | Type | Name | Description |
|------|------|------|-------------|
| 11 | func | `get_position_ids(config)` | Compute digit and arrow position IDs from config |

---

### `watermeter/__main__.py` (3 lines) -- Entry point

Calls `app.main()`.

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

### `roi-config.js` (1594 lines, inline in template)
- Setup mode (step 0): `testImageSource` L4
- Canvas/overlay state L50-85, mouse events L86-245
- Drawing: `drawCrosshair` L246, `drawBox` L271, `render` L312
- Markers: `selectMarkerInPicture` L430, `updateMarkerInputs` L437, `updateMarkerFromInput` L445, `updateMarkerPreview` L457, `saveMarkers` L495, `restartMarkers` L534, `changeMarkers` L548
- Digits: `updateDigitBoxes` L610, `selectDigitInPicture` L673, `runDigitInference` L749, `saveDigits` L806, `changeDigits` L865
- Completed steps: `updateCompletedSteps` L913
- Analogs: `updateAnalogBoxes` L1016, `selectAnalogInPicture` L1080, `runAnalogInference` L1156, `saveAnalogs` L1213, `changeAnalogs` L1272
- Rotation: `getTotalRotation` L1332, `saveRotation` L1369, `changeRotation` L1416
- Image loading: `fetchAndReload` L1436, `loadImage` L1462, `loadConfig` L1488

### `mqtt-config.js` (387 lines)
- IIFE module `MqttConfig` with public API: `init` L368, `loadConfig` L113, `save` L163, `edit` L288, `testConnection` L297
- Helpers: `_getVal` L7, `_setVal` L12, `_setChecked` L17, `_show` L22, `_hide` L27, `_getCheckedRadio` L32, `_setCheckedRadio` L40
- Trigger mode: `_setupTriggerModeRadios` L51, `_updateTriggerFields` L63
- HA toggle: `_setupHaToggle` L76
- Password toggle: `_setupPasswordToggle` L92
- Auto-init on DOMContentLoaded L383

### `config-editor.js` (249 lines)
`initEditor` L17, `updateStatus` L78, `loadConfig` L93, `saveConfig` L118, `showMessage` L170, `toggleHaPublish` L181, `loadHaPublishState` L203, `setMeterValue` L219

---

## Static CSS: `watermeter/static/style.css` (1777 lines)

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

### `benchmark_digits.py` (427 lines)
| Line | Type | Name | Description |
|------|------|------|-------------|
| 21 | func | `check_cuda_available()` | Detect CUDA availability for ONNX Runtime |
| 62 | class | `DigitClassifier` | Lightweight classifier supporting ONNX+CUDA or OpenVINO |
| 82 | func | `preprocess` | Resize and normalize image |
| 91 | func | `predict` | Return (predicted_class, confidence, inference_time) |
| 114 | func | `parse_model_filename(filepath)` | Parse model filename to extract metadata |
| 138 | func | `generate_classes(num_classes)` | Generate digit class labels |
| 154 | func | `collect_test_images(dataset_path)` | Collect images organized by ground truth label |
| 176 | func | `calculate_accuracy(predictions, expected_label)` | Compute accuracy by exact label match |
| 192 | func | `discover_models(model_dir, use_onnx)` | Discover model files in directory |
| 215 | func | `main()` | Entry: discover, load, benchmark, report |

### `benchmark_arrows.py` (446 lines)
| Line | Type | Name | Description |
|------|------|------|-------------|
| 22 | func | `check_cuda_available()` | Detect CUDA availability |
| 30 | class | `ArrowClassifier` | Arrow classifier for ONNX or OpenVINO |
| 82 | func | `parse_model_filename(filepath)` | Parse model filename |
| 107 | func | `generate_classes(num_classes)` | Generate arrow class labels |
| 129 | func | `round_to_class(value, num_classes)` | Round float to nearest arrow class |
| 164 | func | `collect_test_images(dataset_path)` | Collect images by ground truth |
| 189 | func | `calculate_accuracy(predictions, expected_label)` | Compute accuracy |
| 205 | func | `discover_models(model_dir, use_onnx)` | Discover model files |
| 228 | func | `main()` | Entry: discover, load, benchmark, report |

### `train_digits.py` (499 lines)
| Line | Type | Name | Description |
|------|------|------|-------------|
| 55 | func | `analyze_dataset(dataset_dir)` | Print dataset class distribution |
| 79 | func | `create_data_loaders(dataset_dir, resolution, batch_size)` | Build train/val DataLoaders |
| 130 | func | `compute_class_weights(dataset, train_idx, device)` | Compute balanced class weights |
| 163 | func | `train_model(model_name, resolution, epochs, ...)` | Full training loop with export |
| 423 | func | `main()` | CLI entry point |

### `train_arrows.py` (635 lines)
| Line | Type | Name | Description |
|------|------|------|-------------|
| 29 | func | `set_all_seeds(seed)` | Set all RNG seeds |
| 41 | func | `worker_init_fn(worker_id)` | DataLoader worker seed init |
| 81 | func | `create_subsampled_dataset(gt_dir, dataset_dir, step)` | Build subsampled arrow dataset |
| 146 | func | `analyze_dataset(dataset_dir)` | Print dataset distribution |
| 175 | func | `create_data_loaders(dataset_dir, resolution, batch_size)` | Build train/val DataLoaders |
| 231 | func | `compute_class_weights(dataset, train_idx, device)` | Compute balanced class weights |
| 264 | func | `train_model(model_name, resolution, epochs, ...)` | Full training loop with export |
| 524 | func | `main()` | CLI entry point |

---

## Scripts: `scripts/`

### `scripts/backfill_training_samples.py` (88 lines)
| Line | Type | Name | Description |
|------|------|------|-------------|
| 27 | func | `prettify_arch(codename)` | Convert arch codename to display name |
| 37 | func | `backfill()` | Backfill training_samples, val_samples, architecture_display into metadata.json |

### `scripts/tune_synthetic.py` (191 lines)
| Line | Type | Name | Description |
|------|------|------|-------------|
| 34 | func | `generate_mini_dataset(tmpdir, data_type, count_per_class)` | Generate small synthetic dataset |
| 41 | func | `train_mini_model(dataset_dir, num_classes, resolution, epochs)` | Train tiny model on synthetic data |
| 96 | func | `evaluate_against_real(model, classes, input_dir, resolution)` | Evaluate model against real photos |
| 131 | func | `main()` | CLI entry: generate, train, evaluate |

---

## Tests: `tests/`

### `tests/conftest.py` (113 lines) -- Root-level shared fixtures

| Line | Type | Name | Description |
|------|------|------|-------------|
| 17 | func | `pytest_addoption(parser)` | Adds `--base-url`, `--image`, `--no-build` CLI options |
| 36 | fixture | `sample_config_yaml` | Minimal valid YAML config string |
| 82 | fixture | `sample_config_dict` | Config as plain dict |

---

### Unit Tests: `tests/unit/`

#### `tests/unit/conftest.py` (129 lines) -- Unit test fixtures

| Line | Type | Name | Description |
|------|------|------|-------------|
| 26 | func | `_install_mock_modules()` | Installs mocks for cv2, paho, openvino |
| 56 | fixture | `mock_service` | Mock WatermeterService with current_state and config |
| 80 | fixture | `test_client` | FastAPI TestClient with mocked services and tmp_path |

#### `tests/unit/test_api_routes.py` (266 lines)
- `TestConfigEndpoints` L13, `TestTrainingStatus` L83, `TestModelEndpoints` L103, `TestLabelValidation` L157

#### `tests/unit/test_arrow_regression.py` (431 lines)
- `TestRegressionArrowDataset` L131, `TestStratifiedSplitRegression` L178, `TestRegressionPredict` L220, `TestRegressor` L259, `TestDetectTrainingMode` L321, `TestTrainingConfigValidation` L372, `TestValidateModelConfigContinuous` L407

#### `tests/unit/test_circular_error.py` (52 lines)
- `TestCircularError` L5: shortest-path error on circular scale

#### `tests/unit/test_config_reload.py` (165 lines)
- `TestReloadConfig` L50: config reload behavior

#### `tests/unit/test_config_utils.py` (165 lines)
- `TestLoadSaveConfig` L17, `TestConfigString` L50, `TestUpdateConfig` L81, `TestValidateConfig` L113, `TestConfigSchema` L153

#### `tests/unit/test_confirmation.py` (791 lines)
- `TestShouldRequestConfirmation` L166, `TestPublishConfirmationRequest` L241, `TestHandleConfirmationResponse` L358, `TestConfirmationTimeout` L478, `TestGetConfirmationStatus` L566, `TestResetClearsConfirmation` L615, `TestMqttMessageRouting` L635, `TestMqttConnectSubscription` L674, `TestDisabledMode` L711, `TestProcessReadingSkipsPending` L731, `TestRejectWithNonePreviousValue` L765

#### `tests/unit/test_cross_arrow_consistency.py` (379 lines)
- `TestCheckCrossArrowConsistency` L64, `TestCrossArrowIntegration` L247

#### `tests/unit/test_env_var_substitution.py` (74 lines) -- NEW
- `TestResolveEnvVars` L6: string, dict, list, nested substitution; missing vars kept; non-string passthrough

#### `tests/unit/test_image_hash.py` (190 lines)
- `TestComputeDhash` L9, `TestHammingDistance` L81, `TestHashCache` L100

#### `tests/unit/test_leak_detection.py` (322 lines)
- `TestCreateTransforms` L6, `TestCheckSustainedConsumption` L101, `TestLeakWarningMqtt` L208, `TestLeakWarningStateManagement` L268

#### `tests/unit/test_leak_detector.py` (115 lines)
- `TestLeakDetectorInit` L35, `TestLeakDetectorCheck` L42

#### `tests/unit/test_marker_alignment.py` (478 lines)
- `TestFewerThanTwoMarkers` L196, `TestMissingTemplateFiles` L213, `TestLowConfidenceMatch` L239, `TestAlreadyAlignedImage` L267, `TestShiftedImageCorrected` L299, `TestTemplateCaching` L346, `TestCacheInvalidation` L374, `TestSearchRegionNearEdge` L413

#### `tests/unit/test_mislabel.py` (714 lines)
- `TestScanMislabeled` L26, `TestConfirmMislabeled` L275, `TestMakeThumbnailBase64` L426, `TestMislabelAPIEndpoints` L451

#### `tests/unit/test_model_manager.py` (271 lines)
- `TestModelManagerValidation` L31, `TestModelManagerCRUD` L62, `TestModelManagerActivation` L132, `TestModelManagerHelpers` L229

#### `tests/unit/test_mqtt_routes.py` (360 lines) -- NEW
- `TestMqttGetConfig` L8: returns 200, returns sections, returns raw env tokens, correct broker value, 404 on missing config
- `TestMqttPostConfig` L96: saves config, calls reload_config, returns reconnected message, persists to file
- `TestMqttTestConnection` L199: missing broker 400, success ok, refused error, OSError error, resolves env vars, warns on unresolved, disconnects after test, uses 5s keepalive

#### `tests/unit/test_persistence.py` (93 lines)
- `TestStateStore` L11

#### `tests/unit/test_photo_master.py` (109 lines)
- `TestPointerExtractor` L29, `TestArrowCompositor` L65, `TestBuildArrowCompositor` L97

#### `tests/unit/test_plausibility.py` (163 lines)
- `TestCheckConsistency` L46, `TestValidatePlausibility` L100

#### `tests/unit/test_position_utils.py` (131 lines)
- `TestGetPositionIds` L6

#### `tests/unit/test_prune.py` (653 lines)
- `TestClusterImagesByHash` L13, `TestSelectPruneCandidates` L88, `TestComputePrunePreview` L150, `TestConfirmPrune` L265, `TestConfirmPrunePathTraversal` L376, `TestHashCacheExtensions` L458, `TestPruneAPIEndpoints` L501

#### `tests/unit/test_pure_functions.py` (136 lines)
- `TestSafeSubpath` L16, `TestGenerateArrowClasses` L60, `TestRoundToArrowClass` L97

#### `tests/unit/test_rate_tracker.py` (168 lines)
- `TestRateTrackerInit` L10, `TestRateTrackerAdd` L31, `TestRateTrackerMutations` L64, `TestRateTrackerAverageRate` L113, `TestRateTrackerMaxSize` L153

#### `tests/unit/test_synthetic_digit_renderer.py` (138 lines)
- `TestDigitRenderer`: render returns PIL image, correct size (20x32), RGB, all 10 classes, white background, dark pixels, invalid class raises

#### `tests/unit/test_synthetic_arrow_renderer.py` (80 lines)
- `TestArrowRenderer`: render returns PIL image, 100x100, RGB, all 100 classes, red pixels, different classes differ, invalid class raises

#### `tests/unit/test_synthetic_transforms.py` (114 lines)
- `TestTransformPipeline`: returns PIL, preserves size, changes image, same seed same result, arrow mode fisheye, digit mode no fisheye

#### `tests/unit/test_synthetic_generator.py` (194 lines)
- `TestSyntheticGenerator`: generate digits/arrows/both, valid JPEG, reproducible seed, progress callback, synth_ prefix, no overwrite
- `TestDeleteSynthetic`: removes synth files, preserves real, returns correct count

#### `tests/unit/test_synthetic_routes.py` (165 lines)
- `TestSyntheticRoutes`: generate endpoint, returns job_id, invalid type 422, status fields, delete endpoint, 409 conflict, training conflict

#### `tests/unit/test_training_augmentation.py` (44 lines)
- `TestCreateTransforms` L6: verifies transform pipeline output types and sizes

#### `tests/unit/test_training_config.py` (85 lines)
- `TestTrainingConfigLearningRate` L6: validates learning rate field in TrainingConfig

#### `tests/unit/test_trigger_mode.py` (416 lines)
- `TestTriggerConfigSchema` L45, `TestTriggerConfigParsing` L65, `TestTriggerModeDefaults` L101, `TestCyclicLoop` L125, `TestMqttSubscriptionConditional` L234, `TestMqttV2Api` L293

#### `tests/unit/test_value_correction.py` (721 lines)
- `TestPredictDetailed` L97, `TestHelperMethods` L151, `TestConsistencyImprovement` L234, `TestSignalScoring` L290, `TestCorrectionEngine` L467

---

### Integration Tests: `tests/integration/`

#### `tests/integration/conftest.py` (186 lines) -- Docker-based integration fixtures

| Line | Type | Name | Description |
|------|------|------|-------------|
| 27 | func | `_find_free_port()` | Find free TCP port |
| 35 | func | `_image_exists(image)` | Check if Docker image exists locally |
| 44 | func | `_docker_build(image, build_dir)` | Build Docker image |
| 57 | func | `_docker_run(image, host_port)` | Start fresh container; return name |
| 76 | func | `_docker_cp(container, src, dst)` | Copy files into container |
| 84 | func | `_docker_rm(container)` | Stop and remove container |
| 92 | func | `_wait_healthy(url, timeout)` | Poll /health until 200 |
| 107 | func | `_copy_ground_truth(container)` | Copy local ground truth into test container |
| 121 | fixture | `base_url` | Session-scoped: start container or use `--base-url` |
| 176 | fixture | `api` | Session-scoped httpx client with base_url |
| 183 | fixture | `unique_id` | Generate unique ID for test isolation |

#### `tests/integration/test_smoke.py` (41 lines)
- `test_health_endpoint`, `test_status_endpoint`, `test_dashboard_page`, `test_training_page`, `test_training_status_endpoint`

#### `tests/integration/test_config.py` (56 lines)
- `test_get_config`, `test_get_config_schema`, `test_save_invalid_yaml`, `test_config_roundtrip`

#### `tests/integration/test_models.py` (51 lines)
- `test_list_architectures`, `test_list_architectures_short_query_returns_empty`, `test_list_digits_models`, `test_list_arrows_models`, `test_training_data_stats`, `test_model_not_found`

#### `tests/integration/test_training.py` (165 lines)
- `test_training_e2e` L58, `test_training_cancel` L106, `test_training_queue` L134

#### `tests/integration/test_benchmark.py` (148 lines)
- `test_benchmark_digits` L72, `test_benchmark_arrows` L103, `test_benchmark_cancel` L124

#### `tests/integration/test_synthetic.py` (105 lines)
- `test_synthetic_status_idle`, `test_synthetic_generation_digits`, `test_synthetic_cannot_run_twice` (409), `test_synthetic_delete_nonexistent`, `test_synthetic_invalid_type_on_delete`

---

### Regression Tests: `tests/regression/`

#### `tests/regression/test_config_comments.py` (39 lines)
- `test_inline_comment_preserved`, `test_block_comment_preserved`, `test_comment_survives_file_round_trip`

#### `tests/regression/test_nan_class_label.py` (22 lines)
- `test_arrow_classes_do_not_contain_nan`: verifies NAN handling in arrow class generation

---

### Export Tests: `tests/export/`

#### `tests/export/conftest.py` (35 lines)
- `unmock_openvino` (autouse): removes mocked openvino from sys.modules before each test; forces real modules

#### `tests/export/test_export_openvino.py` (80 lines)
- `TestExportToOpenvino` L8: tests dual-path OpenVINO export from PyTorch model

---

## `.claude/agents/` -- Subagent Definitions

| File | Name | Model | Max Turns | Purpose |
|------|------|-------|-----------|---------|
| `debugger.md` | debugger | sonnet | 10 | Test failures, stack traces, runtime errors |
| `dev.md` | dev | sonnet | 15 | All implementation: features, refactoring, bugs |
| `frontend.md` | frontend | sonnet | 15 | Templates (Jinja2), HTMX, CSS styling |
| `planner.md` | planner | opus | 12 | Codebase exploration, task doc creation, architecture |
| `researcher.md` | researcher | sonnet | 8 | Library docs, API research, best practices |
| `reviewer-light.md` | reviewer-light | sonnet | 8 | Trivial/small-diff review: config, single-file fixes |
| `reviewer.md` | reviewer | opus | 8 | Complex multi-file review, security audit, architecture |
| `tester.md` | tester | sonnet | 10 | Unit/regression tests, Playwright browser testing (port 8002) |
