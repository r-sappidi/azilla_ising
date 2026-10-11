function [x_o] = M1_SB(x_i,J_i,N,a,b,c,decay,noise_stream)

    num_meshes = 4;

    if mod(N,num_meshes) ~= 0
        error('N must be divisible by 4');
    end

    spins_per_mesh = N/num_meshes;

    x_o = zeros(N,1);


    for mesh = 1:num_meshes

        % Global spin range
        start = (mesh-1)*spins_per_mesh + 1;
        stop  = mesh*spins_per_mesh;

        % Global offset
        offset = start - 1;


        x_o(start:stop) = M0_SB( ...
            x_i, ...
            J_i(start:stop,:), ...
            spins_per_mesh, ...
            a,b,c, ...
            decay, ...
            noise_stream(start:stop), ...
            offset);

    end

end