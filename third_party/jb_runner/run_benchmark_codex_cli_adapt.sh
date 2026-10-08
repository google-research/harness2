#!/bin/bash

# Derived from the upstream JobBench runner; see PROVENANCE.md for changes.

# Codex CLI Benchmark Runner (JobBench)
# Runs each profession/taskN under jobbench_dataset using Codex CLI exec mode
# For each model, a full independent task folder is copied to /tmp; the model works there
# When the task finishes, output is moved to model_output/{model_name}/ and the temp dir is deleted

set -e


# The adapter supplies a pinned executable, model, and CODEX_HOME provider config.

# Which split of the dataset to run against (main | easy). Defaults to main.
SPLIT="${SPLIT:-main}"
# Target task root (the directory containing all profession subdirectories)
TASKS_BASE_DIR="${TASKS_BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/dataset/${SPLIT}}"
# Optional run label appended to output directory names to avoid clobbering past runs
RUN_LABEL="${RUN_LABEL:-}"
# TASK_IDS restricts discovery to space-separated profession/taskN identifiers.
find_task_dirs() {
    if [[ -n "${TASK_IDS:-}" ]]; then
        local tid
        for tid in $TASK_IDS; do
            local d="${TASKS_BASE_DIR}/${tid}/task_folder"
            if [[ -d "$d" ]]; then
                echo "$d"
            else
                echo "[WARN] TASK_IDS entry has no task_folder: $d" >&2
            fi
        done
        return
    fi
    find "$TASKS_BASE_DIR" -mindepth 3 -maxdepth 3 -type d -name "task_folder" -path "*/task[0-9]*/task_folder" 2>/dev/null | sort
}

# Model identifiers are supplied by the adapter.
read -r -a BENCHMARK_MODELS_ARRAY <<< "${BENCHMARK_MODELS:?Set BENCHMARK_MODELS}"

# Concurrency control: max concurrent tasks per model
MAX_CONCURRENT_PER_MODEL="${MAX_CONCURRENT_PER_MODEL:-1}"

# Per-task timeout in seconds
TIMEOUT_PER_TASK="${TIMEOUT_PER_TASK:-3600}"  # default 60 minutes

# Structured trajectory output directory name (relative to each task directory)
TRAJ_DIR_NAME="${TRAJ_DIR_NAME:-model_traj}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${TASKS_BASE_DIR}/.harness2-logs"

mkdir -p "$LOG_DIR"

TEMP_WORKSPACE_BASE="/tmp/codex_benchmark_workspace"
mkdir -p "$TEMP_WORKSPACE_BASE"

# Recursively terminate descendants (including process groups) to clean up on Ctrl+C
terminate_descendants() {
    local parent_pid="$1"
    local signal_name="$2"
    local children

    children=$(pgrep -P "$parent_pid" 2>/dev/null || true)
    for child_pid in $children; do
        terminate_descendants "$child_pid" "$signal_name"

        # Kill the child's process group first, then the child itself
        local child_pgid
        child_pgid=$(ps -o pgid= -p "$child_pid" 2>/dev/null | tr -d ' ')
        if [[ -n "$child_pgid" ]]; then
            kill "-${signal_name}" "-${child_pgid}" 2>/dev/null || true
        fi
        kill "-${signal_name}" "$child_pid" 2>/dev/null || true
    done
}

handle_interrupt() {
    local sig="${1:-INT}"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Received ${sig}, terminating spawned benchmark processes..."

    terminate_descendants "$$" "TERM"
    sleep 1
    terminate_descendants "$$" "KILL"

    exit 130
}

trap 'handle_interrupt INT' INT
trap 'handle_interrupt TERM' TERM


# Check whether a task has already completed (output dir exists and is non-empty)
is_task_completed() {
    local output_dir="$1"
    if [[ -d "$output_dir" ]] && [[ -n "$(ls -A "$output_dir" 2>/dev/null)" ]]; then
        return 0  # completed
    fi
    return 1  # not completed
}

# Sanitize a model name for use in filenames and directory names
get_safe_model_name() {
    local model="$1"
    echo "$model" | tr '/' '_' | tr '.' '-'
}

# Sanitize a run label for use as a directory suffix
get_safe_run_label() {
    local label="$1"
    echo "$label" | tr '/ ' '__' | tr '.' '-'
}

# Preserve the adapter's LABEL so outputs and trajectories use its run namespace.
get_output_dir_model_name() {
    local model="$1"
    if [[ -n "${LABEL:-}" ]]; then
        echo "$LABEL"
        return
    fi
    local safe_name
    safe_name=$(get_safe_model_name "$model")
    local output_name="${safe_name}-codexcli"

    if [[ -n "${RUN_LABEL:-}" ]]; then
        output_name="${output_name}-$(get_safe_run_label "$RUN_LABEL")"
    fi

    echo "$output_name"
}

# Trajectory directory model name (kept consistent with the output directory)
get_traj_dir_model_name() {
    local model="$1"
    get_output_dir_model_name "$model"
}

# Extract the first session/thread id from a JSONL trajectory
extract_session_id_from_traj() {
    local traj_file="$1"
    python3 - "$traj_file" <<'PY'
import json
import sys

path = sys.argv[1]
keys = {"session_id", "sessionID", "thread_id", "threadID"}

def walk(obj):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str) and value:
                print(value)
                raise SystemExit(0)
        for value in obj.values():
            walk(value)
    elif isinstance(obj, list):
        for item in obj:
            walk(item)

try:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                walk(json.loads(line))
            except SystemExit:
                raise
            except Exception:
                continue
except FileNotFoundError:
    pass
PY
}

# Append a structured attempt-index row for later batch analysis
append_attempt_index() {
    local index_file="$1"
    local task_name="$2"
    local model_id="$3"
    local model_name="$4"
    local attempt="$5"
    local status="$6"
    local exit_code="$7"
    local timeout_seconds="$8"
    local session_id="$9"
    local started_at="${10}"
    local finished_at="${11}"
    local traj_file="${12}"

    if [[ ! -f "$index_file" ]]; then
        printf 'task_name\tmodel_id\tmodel_name\tattempt\tstatus\texit_code\ttimeout_seconds\tsession_id\tstarted_at\tfinished_at\ttraj_file\n' > "$index_file"
    fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$task_name" \
        "$model_id" \
        "$model_name" \
        "$attempt" \
        "$status" \
        "$exit_code" \
        "$timeout_seconds" \
        "$session_id" \
        "$started_at" \
        "$finished_at" \
        "$traj_file" >> "$index_file"
}

# Run a single task with the given model
run_task() {
    local task_dir="$1"
    local model="$2"
    local model_name="$3"  # e.g. gpt-5.4
    local task_parent_dir="$(dirname "$task_dir")"  # the taskN directory
    local profession=$(basename "$(dirname "$task_parent_dir")")
    local task_name="${profession}_$(basename "$task_parent_dir")"
    local safe_model_name
    safe_model_name=$(get_safe_model_name "$model_name")
    local output_model_name
    output_model_name=$(get_output_dir_model_name "$model_name")
    local traj_model_name
    traj_model_name=$(get_traj_dir_model_name "$model_name")

    # Final output directory
    local final_output_dir="${task_parent_dir}/model_output/${output_model_name}"
    local traj_output_dir="${task_parent_dir}/${TRAJ_DIR_NAME}/${traj_model_name}"
    local traj_index_file="${traj_output_dir}/attempt_index.tsv"
    local log_file="${LOG_DIR}/jb_${task_name}_${safe_model_name}_$(date +%Y%m%d_%H%M%S).log"
    local attempt=1
    local attempt_stamp
    attempt_stamp="$(date +%Y%m%d_%H%M%S)"
    local attempt_label="attempt${attempt}_${attempt_stamp}"
    local attempt_traj_file="${traj_output_dir}/${task_name}_${traj_model_name}_${attempt_label}.jsonl"
    local attempt_started_at
    attempt_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Resumable: skip tasks that already have output
    if is_task_completed "$final_output_dir"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIP (already completed): $task_name ($model_name)"
        return 0
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting: $task_name ($model_name)"

    # Temp workspace: copy the whole task_folder into /tmp
    local temp_workspace="${TEMP_WORKSPACE_BASE}/${task_name}_${safe_model_name}_${BASHPID}"
    local temp_task_folder="${temp_workspace}/task_folder"
    local temp_output_dir="${temp_workspace}/output"

    # Create the temp workspace and copy the task_folder
    mkdir -p "$temp_workspace"
    cp -r "$task_dir" "$temp_task_folder"
    mkdir -p "$temp_output_dir"
    mkdir -p "$traj_output_dir"

    # Install workspace harness files; sub_agents are configured through CODEX_HOME.
    if [[ -n "${HARNESS_RENDER_DIR:-}" ]]; then
        local harness_entry
        for harness_entry in AGENTS.md .agents scripts .codex; do
            if [[ -e "${HARNESS_RENDER_DIR}/${harness_entry}" ]]; then
                cp -r "${HARNESS_RENDER_DIR}/${harness_entry}" "${temp_workspace}/${harness_entry}"
            fi
        done
        echo "Harness render dir: ${HARNESS_RENDER_DIR}" | tee -a "$log_file"
    fi

    # Make sure TASK_INSTRUCTIONS.txt exists
    local instructions_file="${temp_task_folder}/TASK_INSTRUCTIONS.txt"
    if [[ ! -f "$instructions_file" ]]; then
        echo "[ERROR] TASK_INSTRUCTIONS.txt not found in $temp_task_folder" | tee -a "$log_file"
        rm -rf "$temp_workspace"
        return 1
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running Codex CLI for: $task_name ($model_name)" | tee -a "$log_file"
    echo "Original task folder: $task_dir" | tee -a "$log_file"
    echo "Temp workspace: $temp_workspace" | tee -a "$log_file"
    echo "Temp task folder: $temp_task_folder" | tee -a "$log_file"
    echo "Temp output directory: $temp_output_dir" | tee -a "$log_file"
    echo "Model: $model" | tee -a "$log_file"
    echo "Final output directory: $final_output_dir" | tee -a "$log_file"
    echo "Trajectory directory: $traj_output_dir" | tee -a "$log_file"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Attempt $attempt trajectory file: $attempt_traj_file" | tee -a "$log_file"

    # Build the prompt message
    local prompt_msg="=== TASK FOLDER ===
${temp_task_folder}

=== INSTRUCTIONS ===
1. Read the TASK_INSTRUCTIONS.txt file in the task folder above
2. Based on the Reference Files section in TASK_INSTRUCTIONS.txt, read the corresponding files from the same task folder using appropriate tools.
3. Complete the task as specified in TASK_INSTRUCTIONS.txt
4. Only save the final deliverables to the output directory specified below. Do not save any intermediate or temporary files.

=== OUTPUT DIRECTORY ===
${temp_output_dir}

IMPORTANT:
- All reference files are in the task folder: ${temp_task_folder}
- Only save the final deliverables to the output directory ${temp_output_dir}. Do not save any intermediate or temporary files.
- You MUST only access files within ${temp_workspace} or search online for new reference files if you find needed. Do NOT access any files or directories in this system outside of this path.
- If you encounter ambiguous or conflicting information, analyze the conflict, explain your reasoning, and justify the approach you choose.
- If a file cannot be read directly (e.g., .xlsx, .docx, .db, .pptx), use appropriate tools, MCP servers, or code to extract and process its contents."

    local actual_model="$model"
    local reasoning_effort="high"

    # Run Codex CLI via Python to avoid shell-escaping issues
    # Disable set -e temporarily so we can capture the real exit code and clean up
    set +e
    timeout "${TIMEOUT_PER_TASK}s" python3 - "$prompt_msg" "$actual_model" "$temp_workspace" "$log_file" "$reasoning_effort" "$attempt_traj_file" << 'PYTHON_EXEC'
import os
import subprocess
import sys

prompt_content = sys.argv[1]
model = sys.argv[2]
working_dir = sys.argv[3]
log_file = sys.argv[4]
reasoning_effort = sys.argv[5]
traj_file = sys.argv[6]

# Enable editable hooks and preserve the ephemeral session as JSON.
# Closed stdin prevents waiting for input on an inherited pipe.

with open(log_file, 'a', encoding='utf-8') as log, open(traj_file, 'w', encoding='utf-8') as traj:
    result = subprocess.run(
        [
            os.environ['HARNESS2_CODEX_BIN'], 'exec',
            prompt_content,
            '--model', model,
            '-c', f'model_reasoning_effort=\"{reasoning_effort}\"',
            '--skip-git-repo-check',
            '--dangerously-bypass-approvals-and-sandbox',
            '--dangerously-bypass-hook-trust',
            '--ephemeral',
            '--json',
            '-C', working_dir,
            '--add-dir', working_dir,
        ],
        cwd=working_dir,
        stdin=subprocess.DEVNULL,
        stdout=traj,
        stderr=log,
        text=True
    )

sys.exit(result.returncode)
PYTHON_EXEC

    local exit_code=$?
    set -e
    local attempt_finished_at
    attempt_finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local attempt_status="failed"
    local session_id
    session_id="$(extract_session_id_from_traj "$attempt_traj_file")"

    if [[ $exit_code -eq 0 ]] && [[ ! -d "$temp_output_dir" || -z "$(ls -A "$temp_output_dir" 2>/dev/null)" ]]; then
        exit_code=1
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] EMPTY OUTPUT: Task exited successfully but produced no files: $task_name ($model_name)" | tee -a "$log_file"
    fi

    if [[ $exit_code -eq 0 ]]; then
        attempt_status="success"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Task completed successfully: $task_name ($model_name)" | tee -a "$log_file"
    elif [[ $exit_code -eq 124 ]]; then
        attempt_status="timeout"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Task timed out after ${TIMEOUT_PER_TASK}s: $task_name ($model_name)" | tee -a "$log_file"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Task failed with exit code $exit_code: $task_name ($model_name)" | tee -a "$log_file"
    fi

    append_attempt_index \
        "$traj_index_file" \
        "$task_name" \
        "$model" \
        "$traj_model_name" \
        "$attempt" \
        "$attempt_status" \
        "$exit_code" \
        "$TIMEOUT_PER_TASK" \
        "${session_id:-}" \
        "$attempt_started_at" \
        "$attempt_finished_at" \
        "$attempt_traj_file"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Attempt $attempt session: ${session_id:-unknown}" | tee -a "$log_file"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Attempt $attempt structured trajectory saved to: $attempt_traj_file" | tee -a "$log_file"

    # Task done: move the output directory to its final location
    if [[ -d "$temp_output_dir" ]] && [[ -n "$(ls -A "$temp_output_dir" 2>/dev/null)" ]]; then
        mkdir -p "$final_output_dir"
        mv "$temp_output_dir"/* "$final_output_dir"/
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Output moved to: $final_output_dir" | tee -a "$log_file"
        echo "Output files:" | tee -a "$log_file"
        ls -la "$final_output_dir" | tee -a "$log_file"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] No output files generated in $temp_output_dir" | tee -a "$log_file"
    fi

    # Clean up the temp workspace
    rm -rf "$temp_workspace"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleaned up temp workspace: $temp_workspace" | tee -a "$log_file"

    return $exit_code
}

# Run all tasks for a single model
run_model_tasks() {
    local model="$1"
    local model_name="$2"
    local task_dirs=("${@:3}")
    local safe_model_name=$(get_safe_model_name "$model_name")

    local pids=()
    local failed=0
    local completed=0
    local total=${#task_dirs[@]}

    # Reap finished tasks; keep only still-running PIDs
    cleanup_finished_pids() {
        local new_pids=()
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                new_pids+=("$pid")
            else
                if ! wait "$pid"; then
                    ((failed++)) || true
                fi
                ((completed++)) || true
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$model_name] Progress: $completed/$total completed, $failed failed"
            fi
        done
        pids=("${new_pids[@]}")
    }

    for task_dir in "${task_dirs[@]}"; do
        local _parent=$(dirname "$task_dir")
        local task_name="$(basename "$(dirname "$_parent")")_$(basename "$_parent")"
        local final_output_dir="${_parent}/model_output/$(get_output_dir_model_name "$model_name")"

        # Pre-check completion to avoid forking and the 2s polling delay
        if is_task_completed "$final_output_dir"; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIP (already completed): ${task_name} ($model_name)"
            ((completed++)) || true
            continue
        fi

        # Wait for a free slot
        while [[ ${#pids[@]} -ge $MAX_CONCURRENT_PER_MODEL ]]; do
            cleanup_finished_pids
            if [[ ${#pids[@]} -ge $MAX_CONCURRENT_PER_MODEL ]]; then
                sleep 2
            fi
        done

        run_task "$task_dir" "$model" "$model_name" &
        pids+=($!)
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Launched: ${task_name} ($model_name) - PID: ${pids[-1]} (Running: ${#pids[@]}/$MAX_CONCURRENT_PER_MODEL)"
    done

    # Wait for the remaining tasks to finish
    for pid in "${pids[@]}"; do
        if ! wait "$pid"; then
            ((failed++)) || true
        fi
        ((completed++)) || true
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$model_name] Progress: $completed/$total completed, $failed failed"
    done

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$model_name] All tasks completed. Failed: $failed"
    return $failed
}

# Main entry point
main() {
    echo "=========================================="
    echo "Codex CLI Benchmark Runner (JobBench)"
    echo "Models: ${BENCHMARK_MODELS_ARRAY[*]}"
    echo "Tasks Base Directory: $TASKS_BASE_DIR"
    echo "Run Label: ${RUN_LABEL:-<none>}"
    echo "Temp Workspace: $TEMP_WORKSPACE_BASE"
    echo "Max Concurrent Per Model: $MAX_CONCURRENT_PER_MODEL"
    echo "Timeout Per Task: ${TIMEOUT_PER_TASK}s"
    echo "Structured Trajectory Directory Name: $TRAJ_DIR_NAME"
    echo "=========================================="

    # Check the executable installed by the release setup script.
    echo "Checking Codex CLI availability..."
    "${HARNESS2_CODEX_BIN:?Run scripts/setup_benchmark.py with --substrate cx}" --version 2>&1 || {
        echo "[ERROR] Codex CLI not available. Run scripts/setup_benchmark.py with --substrate cx."
        exit 1
    }

    # cd into the script directory
    cd "$SCRIPT_DIR"

    # Find all task_folder directories
    local task_dirs=($(find_task_dirs))
    local total_tasks=${#task_dirs[@]}

    if [[ $total_tasks -eq 0 ]]; then
        echo "No task_folder directories found in $TASKS_BASE_DIR"
        exit 1
    fi

    local num_models=${#BENCHMARK_MODELS_ARRAY[@]}
    echo "Found $total_tasks task_folder directories"
    echo "Will run ${total_tasks} x ${num_models} = $((total_tasks * num_models)) total tasks"
    echo ""

    # Run each model sequentially (models serial, tasks within a model parallel)
    local failed=0

    for i in "${!BENCHMARK_MODELS_ARRAY[@]}"; do
        local model="${BENCHMARK_MODELS_ARRAY[$i]}"
        local model_name="${BENCHMARK_MODELS_ARRAY[$i]}"
        echo ""
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$((i+1))/${num_models}] Starting benchmark for model: $model_name"
        if ! run_model_tasks "$model" "$model_name" "${task_dirs[@]}"; then
            ((failed++)) || true
        fi
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$((i+1))/${num_models}] Finished model: $model_name"
    done

    echo ""
    echo "=========================================="
    echo "Benchmark Complete (JobBench)"
    echo "Total tasks: $((total_tasks * num_models)) (${total_tasks} tasks x ${num_models} models)"
    echo "Failed models: $failed"
    echo "Logs saved to: $LOG_DIR"
    echo "Output directories:"
    for model in "${BENCHMARK_MODELS_ARRAY[@]}"; do
        local output_name
        output_name=$(get_output_dir_model_name "$model")
        echo "  - ${output_name}: model_output/${output_name}/"
    done
    echo "Trajectory directories:"
    for model in "${BENCHMARK_MODELS_ARRAY[@]}"; do
        local traj_name
        traj_name=$(get_traj_dir_model_name "$model")
        echo "  - ${traj_name}: ${TRAJ_DIR_NAME}/${traj_name}/"
    done
    echo "=========================================="

    # Surface the real overall status: any failed model exits non-zero
    if [[ $failed -gt 0 ]]; then
        return 1
    fi
    return 0
}

# Run main when this script is invoked directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
