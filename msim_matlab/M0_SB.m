function [x_o] = M0_SB(x_i,J_i,N,a,b,c,decay,noise_stream,offset)

    num_clusters = 4;

    if mod(N,num_clusters) ~= 0
        error('N must be divisible by 4');
    end

    spins_per_cluster = N/num_clusters;

    x_o = zeros(N,1);


    for cluster = 1:num_clusters

        % Local indices
        start = (cluster-1)*spins_per_cluster + 1;
        stop  = cluster*spins_per_cluster;


        % Convert local index to global index
        global_offset = offset + start - 1;


        x_o(start:stop) = H1_SB( ...
            x_i, ...
            J_i(start:stop,:), ...
            spins_per_cluster, ...
            a,b,c, ...
            decay, ...
            noise_stream(start:stop), ...
            global_offset);

    end

end