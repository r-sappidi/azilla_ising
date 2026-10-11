function [x_o] = leaf_SB(x_i,J_i,start,stop,a,b,c,decay,noise_stream)

    % Legacy hierarchy-compatible wrapper for the quantized noisy-feedback
    % Ising update.  This is not simulated bifurcation.  Bit-exact RTL/Octave
    % comparisons must use rtl_ising_step, which reproduces the RTL LFSR.

    % Self feedback
    state = a*x_i(start:stop);

    % Coupling
    coupling = b*J_i*x_i;

    % Independent noise per spin
    noise = zeros(stop-start+1,1);
    for k = 1:(stop-start+1)
        % Rademacher (+/-1) noise matches the implemented noise distribution;
        % the stream differs from RTL unless rtl_ising_step is used.
        noise(k) = decay*c*(2*(rand(noise_stream{k},1) >= 0.5)-1);

    end

    % Threshold
    field = state + coupling + noise;
    % RTL resolves an exactly-zero field to +1; MATLAB sign(0) returns zero.
    x_o = 2*(field >= 0)-1;

end
