function generate_matlab_golden(dataset_path, output_path, iteration_count, ...
                                coeff_a, coeff_b, noise_amplitude)
%GENERATE_MATLAB_GOLDEN Independent graph-level Ising state reference.
%
% The implementation intentionally operates on the dataset edge list and the
% mathematical update equation. It does not reproduce the RTL hierarchy,
% packetization, scheduling, or Python model implementation.

  if nargin < 3, iteration_count = 1; end
  if nargin < 4, coeff_a = 0; end
  if nargin < 5, coeff_b = 1; end
  if nargin < 6, noise_amplitude = 0; end

  input_file = fopen(dataset_path, 'r');
  if input_file < 0
    error('cannot open dataset %s', dataset_path);
  end
  header = sscanf(fgetl(input_file), '%d %d');
  if isempty(header)
    error('invalid dataset header in %s', dataset_path);
  end
  spin_count = header(1);
  edges = textscan(input_file, '%d %d %d');
  fclose(input_file);

  rows = double(edges{1});
  columns = double(edges{2});
  weights = double(edges{3});
  if any(rows < 1 | rows > spin_count | columns < 1 | columns > spin_count)
    error('dataset edge index is out of range');
  end
  if mod(spin_count, 32) ~= 0
    error('spin count must be divisible by 32');
  end

  block_count = spin_count / 32;
  current = false(spin_count, 1);
  lfsr = zeros(block_count, 1, 'uint32');
  modulus = bitshift(uint64(1), 32);
  for block = 0:block_count-1
    state_word = bitxor(uint32(2654435769), ...
      uint32(mod(uint64(block) * uint64(2246822507), modulus)));
    state_word = bitxor(state_word, bitshift(state_word, 13));
    state_word = bitxor(state_word, bitshift(state_word, -17));
    state_word = bitxor(state_word, bitshift(state_word, 5));
    for bit = 0:31
      current(block * 32 + bit + 1) = bitget(state_word, bit + 1) ~= 0;
    end

    seed = bitxor(uint32(3518319157), ...
      uint32(mod(uint64(block) * uint64(668265261), modulus)));
    if seed == 0, seed = uint32(1); end
    lfsr(block + 1) = seed;
  end

  output_file = fopen(output_path, 'w');
  if output_file < 0
    error('cannot create golden file %s', output_path);
  end
  fprintf(output_file, '%d %d\n', spin_count, iteration_count);

  for iteration = 1:iteration_count
    spin_sign = 2.0 * double(current) - 1.0;
    contributions = weights .* spin_sign(columns);
    coupling_sum = accumarray(rows, contributions, [spin_count, 1], @sum, 0);

    for block = 1:block_count
      value = lfsr(block);
      feedback = xor(xor(bitget(value, 32), bitget(value, 22)), ...
                     xor(bitget(value, 2), bitget(value, 1)));
      lfsr(block) = bitor(bitshift(value, 1), uint32(feedback));
    end

    noise = zeros(spin_count, 1);
    if noise_amplitude ~= 0
      for spin = 0:spin_count-1
        block = floor(spin / 32) + 1;
        bit = mod(spin, 32) + 1;
        noise(spin + 1) = noise_amplitude * ...
          (2.0 * double(bitget(lfsr(block), bit)) - 1.0);
      end
    end

    local_term = coeff_a * (2.0 * double(current) - 1.0);
    field = local_term + coeff_b * coupling_sum + noise;
    next = field >= 0;
    fprintf(output_file, '%d\n', next);
    current = next;
  end
  fclose(output_file);

  fprintf('MATLAB_GOLDEN spins=%d iterations=%d ones=%d output=%s\n', ...
          spin_count, iteration_count, sum(current), output_path);
end
