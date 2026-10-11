clc; clear; close all;

% Algorithm-level demonstration of the quantized noisy-feedback Ising rule.
% The canonical bit-exact reference is rtl_ising_step.m; the hierarchical
% *_SB wrappers are retained only for compatibility and are not SB dynamics.

GS = 8064;
num_iter = 100;

[J,N] = findJ('g4096_lattice.txt');

a = 3;
b = 1;
noise_amplitude = 8;

% One independent noise source per spin
noise_stream = arrayfun(@(k) ...
    RandStream('Threefry','Seed',k), ...
    1:N, ...
    'UniformOutput', false);

accuracy = zeros(1,1);

for k = 1:1

    J_i = J;

    maxcut = zeros(num_iter,1);

    x = false(N,num_iter);
    x(:,1) = rtl_initial_state(N);
    lfsr = rtl_noise_state(N);

    decay = linspace(1,0,num_iter);

    for i = 2:num_iter
    
        amplitude = floor(noise_amplitude*decay(i-1));
        [x(:,i),lfsr] = rtl_ising_step(x(:,i-1),J_i,lfsr,a,b,amplitude);
    end

    for i = 1:num_iter
        cut_edges = (x(:,i))*transpose(x(:,i)) == -1;
        maxcut(i) = 0.5*sum(sum(J.*cut_edges));
    end

    plot(1:num_iter,maxcut)

    accuracy(k) = 100*maxcut(end)/GS;
    disp(accuracy(k))
    disp(maxcut(end))

end

avg_accuracy = mean(accuracy);
max_accuracy = max(accuracy);
std_accuracy = std(accuracy);
sr_90 = sum(accuracy>=90)/20;
sr_95 = sum(accuracy>=95)/20;
sr_100 = sum(accuracy>=100)/20;
