// Timing-only port of model/azilla_cycle_model/streaming_core.py
// (StreamingCoreIteration + its DirectedMVM) and, as the ``buffered``
// variant, model/azilla_cycle_model/core_iteration.py (CoreIteration).
//
// Arithmetic (weight payloads, accumulators, LFSR, state_next) is omitted:
// the Python exact-event path runs with timing_only=True and all state words
// are zero, so no timing decision depends on data.  Every handshake, state
// machine edge and the shared single-MVM row counter are ported literally.
#pragma once

#include <cstdint>

namespace azilla {

// Row-serial mirror of compute.MVM with the DirectedMVM wrappers.
struct EndpointMvm {
    bool active = false;
    int process_row = 0;
    int request_row = 0;
    bool read_valid = false;
    bool done = false;         // compute.MVM: sticky until the next start
    bool transpose_mode = false;
    bool stream_mode = false;  // streaming_core.DirectedMVM only

    void reset() {
        active = false;
        process_row = 0;
        request_row = 0;
        read_valid = false;
        done = false;
        transpose_mode = false;
        stream_mode = false;
    }
    // compute.MVM.tick (timing-only).
    void base_tick(bool start);
    // streaming_core.DirectedMVM.tick.
    void streaming_tick(bool start, bool transpose, bool stream, bool row_valid);
    // core_iteration.DirectedMVM.tick.
    void buffered_tick(bool start, bool transpose);
};

struct CoreIterationOutputs {
    bool init_done;
    bool weight_init_ready;
    bool iter_done;
    bool job_ready;
    bool state_req_valid;
    int state_req_block_id;
    bool weight_ready;
    bool job_done;
};

struct CoreInputs {
    bool rst = false;
    bool init_start = false;
    bool weight_init_valid = false;
    bool iter_start = false;
    bool partials_done = false;
    bool commit = false;
    bool done = false;
    bool job_valid = false;
    int source = 0;
    bool transpose = false;
    bool state_req_ready = false;
    bool state_rsp_valid = false;
    bool weight_valid = false;
};

class EndpointCore {
public:
    enum CoreState { RESET, INIT, IDLE, ACCUMULATE, FINALIZE, WAIT_COMMIT, COMMIT, DONE };
    enum OffState { OFF_IDLE, STATE_REQ, STATE_WAIT, LOAD, RUN, RETIRE };

    explicit EndpointCore(bool streaming) : streaming_(streaming) { reset(); }
    void reset();
    CoreIterationOutputs outputs() const;
    void tick(const CoreInputs& in);

    bool streaming() const { return streaming_; }

    // Visible state (also read by the pipeline's running-set rule).
    CoreState core_state = RESET;
    OffState off_state = OFF_IDLE;
    bool diagonal_captured = false;
    int off_beat = 0;
    int source = 0;
    bool transpose = false;
    bool job_done = false;
    bool init_done = false;
    int weight_beat_count = 0;
    bool partials_done_pending = false;
    bool done_latched = false;
    EndpointMvm local_mvm;

private:
    bool streaming_;
};

}  // namespace azilla
