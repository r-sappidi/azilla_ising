function lfsr = rtl_noise_state(num_spins)
% Match the deterministic per-core RTL noise seeds.
  if mod(num_spins,32) ~= 0, error('Spin count must be divisible by 32'); end
  lfsr = zeros(num_spins/32,1,'uint32');
  for block = 0:(num_spins/32-1)
    product = mod(uint64(block)*uint64(hex2dec('27d4eb2d')), uint64(4294967296));
    value = bitxor(uint32(hex2dec('d1b54a35')), uint32(product));
    if value == 0, value = uint32(1); end
    lfsr(block+1) = value;
  end
end
