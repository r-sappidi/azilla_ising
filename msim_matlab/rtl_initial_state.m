function state = rtl_initial_state(num_spins)
% Match ising_mesh_tb.initial_state_for_block, including uint32 wrapping.
  if mod(num_spins,32) ~= 0, error('Spin count must be divisible by 32'); end
  state = false(num_spins,1);
  for block = 0:(num_spins/32-1)
    product = mod(uint64(block)*uint64(hex2dec('85ebca6b')), uint64(4294967296));
    value = bitxor(uint32(hex2dec('9e3779b9')), uint32(product));
    value = bitxor(value, bitshift(value,13));
    value = bitxor(value, bitshift(value,-17));
    value = bitxor(value, bitshift(value,5));
    first = block*32+1;
    state(first:first+31) = logical(bitget(value,1:32)).';
  end
end
