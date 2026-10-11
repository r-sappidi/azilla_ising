#include "core_iteration.hpp"

namespace azilla {

namespace {
constexpr int SPINS = 32;
}

// compute.MVM.tick without the dot product (timing_only).
void EndpointMvm::base_tick(bool start) {
    if (start && !active) {
        active = true;
        done = false;
        process_row = 0;
        request_row = 1 % SPINS;
        read_valid = true;
        return;
    }
    if (active && read_valid) {
        if (process_row == SPINS - 1) {
            process_row = 0;
            request_row = 0;
            read_valid = false;
            active = false;
            done = true;
        } else {
            process_row += 1;
            if (request_row != SPINS - 1) request_row += 1;
        }
    }
}

// streaming_core.DirectedMVM.tick (rst is never asserted by the pipeline).
void EndpointMvm::streaming_tick(bool start, bool transpose, bool stream, bool row_valid) {
    if (start && !active) {
        stream_mode = stream;
    } else if (active && stream_mode && !row_valid) {
        return;  // the MVM holds while no accepted row arrives
    }
    if (start && !active) transpose_mode = transpose;
    base_tick(start);
}

// core_iteration.DirectedMVM.tick.
void EndpointMvm::buffered_tick(bool start, bool transpose) {
    if (start && !active) transpose_mode = transpose;
    base_tick(start);
}

void EndpointCore::reset() {
    core_state = RESET;
    weight_beat_count = 0;
    partials_done_pending = false;
    init_done = false;
    done_latched = false;
    local_mvm.reset();
    off_state = OFF_IDLE;
    diagonal_captured = false;
    off_beat = 0;
    source = 0;
    transpose = false;
    job_done = false;
}

CoreIterationOutputs EndpointCore::outputs() const {
    CoreIterationOutputs o;
    o.init_done = init_done;
    o.weight_init_ready = core_state == INIT && weight_beat_count < SPINS;
    o.iter_done = core_state == WAIT_COMMIT;
    o.job_ready = core_state == ACCUMULATE && diagonal_captured && off_state == OFF_IDLE && !partials_done_pending;
    o.state_req_valid = off_state == STATE_REQ;
    o.state_req_block_id = source;
    o.weight_ready = off_state == LOAD;
    o.job_done = off_state == RETIRE;
    return o;
}

void EndpointCore::tick(const CoreInputs& in) {
    const CoreIterationOutputs before = outputs();
    if (in.rst) {
        reset();
        return;
    }
    const CoreState old = core_state;
    const OffState off = off_state;
    const bool old_done = local_mvm.done;
    const bool diagonal_start = old == IDLE && in.iter_start;
    const bool off_start = streaming_ ? (off == STATE_WAIT && in.state_rsp_valid)
                                      : (off == LOAD && in.weight_valid && off_beat == SPINS - 1);
    CoreState next_state = old;
    if (old == RESET && in.init_start) next_state = INIT;
    else if (old == INIT && !before.weight_init_ready) next_state = IDLE;
    else if (diagonal_start) next_state = ACCUMULATE;
    else if (old == ACCUMULATE && diagonal_captured && off == OFF_IDLE && partials_done_pending && !in.job_valid)
        next_state = FINALIZE;
    else if (old == FINALIZE) next_state = WAIT_COMMIT;
    else if (old == WAIT_COMMIT && in.commit) next_state = COMMIT;
    else if (old == COMMIT) next_state = (done_latched || in.done) ? DONE : IDLE;

    const bool offdiagonal = off == LOAD || off == RUN || off == RETIRE;
    if (streaming_) {
        local_mvm.streaming_tick(diagonal_start || off_start, off != OFF_IDLE ? transpose : false, off_start,
                                 in.weight_valid && before.weight_ready);
    } else {
        local_mvm.buffered_tick(diagonal_start || off_start, offdiagonal ? transpose : false);
    }
    const bool writing_diagonal = in.weight_init_valid && before.weight_init_ready;
    const bool writing_off = in.weight_valid && before.weight_ready;
    job_done = false;
    done_latched = done_latched || in.done;
    if (in.iter_start) {
        partials_done_pending = false;
    } else if (in.partials_done) {
        partials_done_pending = true;
    }
    if (writing_diagonal) weight_beat_count += 1;
    if (next_state == IDLE) init_done = true;
    if (diagonal_start) {
        diagonal_captured = false;
        off_state = OFF_IDLE;
    } else if (old == ACCUMULATE) {
        if (!diagonal_captured && old_done) diagonal_captured = true;
        if (off == OFF_IDLE && in.job_valid && before.job_ready) {
            source = in.source;
            transpose = in.transpose;
            off_beat = 0;
            off_state = STATE_REQ;
        } else if (off == STATE_REQ && in.state_req_ready) {
            off_state = STATE_WAIT;
        } else if (off == STATE_WAIT && in.state_rsp_valid) {
            off_state = LOAD;
        } else if (off == LOAD && writing_off) {
            if (off_beat == SPINS - 1) {
                off_beat = 0;
                off_state = RUN;
            } else {
                off_beat += 1;
            }
        } else if (off == RUN && old_done) {
            off_state = RETIRE;
        } else if (off == RETIRE) {
            off_state = OFF_IDLE;
        }
    }
    core_state = next_state;
}

}  // namespace azilla
