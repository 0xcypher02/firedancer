use {
    solana_sdk::{
        bpf_loader_upgradeable,
        bpf_loader_upgradeable::{
            UpgradeableLoaderState,
        },
        system_instruction,
        signature::{Keypair, read_keypair_file, Signer},
        transaction::Transaction,
        commitment_config::CommitmentConfig,
        feature_set::FeatureSet,
        message::Message,
        instruction::{Instruction, AccountMeta},
    },
    solana_client::{
        rpc_client::RpcClient,
    },
    solana_cli::{
        program::calculate_max_chunk_size,
    },
    solana_bpf_loader_program::{
        self,
        syscalls::create_program_runtime_environment_v1
    },
    solana_program_runtime::{
        invoke_context::{InvokeContext},
        compute_budget::ComputeBudget
    },
    solana_rbpf::{
        elf::Executable,
        verifier::RequisiteVerifier
    },
    std::{fs::File, io::Read, sync::Arc, thread, time::Duration},
};


fn read_and_verify_elf(program_location: &str) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let mut file = File::open(program_location)
        .map_err(|err| format!("Unable to open program file: {err}"))?;
    let mut program_data = Vec::new();
    file.read_to_end(&mut program_data)
        .map_err(|err| format!("Unable to read program file: {err}"))?;

    // Verify the program
    let program_runtime_environment = create_program_runtime_environment_v1(
        &FeatureSet::all_enabled(),
        &ComputeBudget::default(),
        true,
        false,
    )
    .unwrap();
    let executable =
        Executable::<InvokeContext>::from_elf(&program_data, Arc::new(program_runtime_environment))
            .map_err(|err| format!("ELF error: {err}"))?;

    executable
        .verify::<RequisiteVerifier>()
        .map_err(|err| format!("ELF error: {err}"))?;

    Ok(program_data)
}


fn main() {
    let validator_url = "http://localhost:8899";
    let client = RpcClient::new_with_commitment(validator_url, CommitmentConfig::confirmed());

    let program_path = "helloworld.so";
    match client.get_slot() {
        Ok(slot) => println!("Current slot: {}", slot),
        Err(e) => eprintln!("Error retrieving slot: {}", e),
    }
    
    let payer_keypair_path = "/home/kbhargava/.config/solana/id.json";
    let payer = read_keypair_file(payer_keypair_path).unwrap();

    let buffer_account = Keypair::new();
    
    let program_keypair = Keypair::new();

    let program_data = read_and_verify_elf(program_path).unwrap();
    let min_rent_exempt_program_data_balance = client.get_minimum_balance_for_rent_exemption(
        UpgradeableLoaderState::size_of_programdata(program_data.len()),
    ).unwrap(); 

    let create_program_account_instruction = bpf_loader_upgradeable::create_buffer(
        &payer.pubkey(),
        &buffer_account.pubkey(),
        &payer.pubkey(),
        min_rent_exempt_program_data_balance,
        program_data.len(),
    );

    let buffer_transaction = Transaction::new_signed_with_payer(
        &create_program_account_instruction.unwrap(),
        Some(&payer.pubkey()),
        &[&payer, &buffer_account],
        client.get_latest_blockhash().unwrap(),
    );

    let result = client.send_and_confirm_transaction(&buffer_transaction);
    println!("New account created with pubkey: {}", buffer_account.pubkey());
    println!("Transaction signature: {}", result.unwrap());

    let blockhash = client.get_latest_blockhash().unwrap();

    let create_msg = |offset: u32, bytes: Vec<u8>| {
        let write_instruction = bpf_loader_upgradeable::write(
            &buffer_account.pubkey(),
            &payer.pubkey(),
            offset,
            bytes,
        );
        return Message::new_with_blockhash(&[write_instruction], Some(&payer.pubkey()), &blockhash);
    };

    
    let chunk_size = calculate_max_chunk_size(&create_msg);
    println!("Chunk size: {}", chunk_size);
    // let chunks = program_data.chunks(chunk_size);
    
    for (chunk, i) in program_data.chunks(chunk_size).zip(0..) {
        let message = create_msg((i * chunk_size) as u32, chunk.to_vec());

        let mut write_transaction = Transaction::new_unsigned(
            message,
        );
        let _ = write_transaction.try_sign(&[&payer], blockhash);
    
        let _ = client.send_and_confirm_transaction(&write_transaction);
    }

    let final_instruction = bpf_loader_upgradeable::deploy_with_max_program_len(
        &payer.pubkey(),
        &program_keypair.pubkey(),
        &buffer_account.pubkey(),
        &payer.pubkey(),
        client.get_minimum_balance_for_rent_exemption(
            UpgradeableLoaderState::size_of_program(),
        ).unwrap(),
        program_data.len(),
    ).unwrap();
    let final_message = Message::new_with_blockhash(&final_instruction, Some(&payer.pubkey()), &blockhash);
    let mut final_tx = Transaction::new_unsigned(final_message.clone());
    let blockhash = client.get_latest_blockhash().unwrap();
    let signers: Vec<&dyn Signer> = vec![&payer, &program_keypair];
    let result = final_tx.try_sign(&signers, blockhash);
    println!("Final transaction signed: {}", result.is_ok());
    let final_result = client.send_and_confirm_transaction(&final_tx);

    println!("Final program deployed: {}", program_keypair.pubkey());
    println!("Transaction signature: {}", final_result.unwrap());

    thread::sleep(Duration::from_secs(10));

    let run_account = Keypair::new();
    let account_data = vec![0u8; 4];
    
    let open_account_instruction = system_instruction::create_account(
        &payer.pubkey(),
        &run_account.pubkey(),
        client.get_minimum_balance_for_rent_exemption(account_data.len()).unwrap(),
        account_data.len() as u64,
        &program_keypair.pubkey(),
    );

    let transaction = Transaction::new_signed_with_payer(
        &[open_account_instruction],
        Some(&payer.pubkey()),
        &[&payer, &run_account],
        client.get_latest_blockhash().unwrap(),
    );
    let result = client.send_and_confirm_transaction(&transaction);
    println!("Transaction Result: {:?}", result.unwrap());

    let account_metas = vec![
        AccountMeta::new(run_account.pubkey(), false),
    ];
    let instruction = Instruction::new_with_bytes(
        program_keypair.pubkey(),
        &account_data,
        account_metas,
    );

    let transaction = Transaction::new_signed_with_payer(
        &[instruction],
        Some(&payer.pubkey()),
        &[&payer],
        client.get_latest_blockhash().unwrap(),
    );

    let result = client.send_and_confirm_transaction(&transaction);
    println!("Transaction Result: {:?}", result.unwrap());
}
